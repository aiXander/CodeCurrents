"""Token counting and commit chunking."""

import re
import sys

from .git_ops import CommitInfo

# Use a 1.2x safety margin — tiktoken undercounts for non-OpenAI models
TOKEN_SAFETY_MARGIN = 1.2

try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")

    def count_tokens(text: str) -> int:
        return int(len(_enc.encode(text, disallowed_special=())) * TOKEN_SAFETY_MARGIN)
except Exception:
    def count_tokens(text: str) -> int:
        return len(text) // 3  # conservative fallback


def _extract_file_list(diff: str) -> str:
    """Extract just the file paths from a diff for use as a summary fallback."""
    files = re.findall(r"^diff --git a/.+? b/(.+?)$", diff, re.MULTILINE)
    if files:
        return "Files changed:\n" + "\n".join(f"  - {f}" for f in files)
    return "(diff too large to include)"


def _truncate_diff(diff: str, max_tokens: int) -> str:
    """Truncate a diff to fit within max_tokens, keeping the beginning and file list."""
    # Try progressively shorter cuts
    lines = diff.splitlines()
    # Binary search for the right cutoff
    lo, hi = 0, len(lines)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        candidate = "\n".join(lines[:mid])
        if count_tokens(candidate) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1

    truncated = "\n".join(lines[:lo])
    file_list = _extract_file_list(diff)
    remaining = len(lines) - lo
    return f"{truncated}\n\n... [TRUNCATED {remaining} lines]\n{file_list}"


def format_commit(commit: CommitInfo, diff: str) -> str:
    """Format a single commit + diff for LLM consumption."""
    return f"--- Commit {commit.hash[:10]} ({commit.date}) ---\n{commit.subject}\n\n{diff}\n"


def chunk_commits(
    commits_with_diffs: list[tuple[CommitInfo, str]],
    target_tokens: int,
) -> list[list[tuple[CommitInfo, str]]]:
    """Group commits into chunks of roughly target_tokens each."""
    if not commits_with_diffs:
        return []

    chunks: list[list[tuple[CommitInfo, str]]] = []
    current_chunk: list[tuple[CommitInfo, str]] = []
    current_tokens = 0

    for commit, diff in commits_with_diffs:
        formatted = format_commit(commit, diff)
        tokens = count_tokens(formatted)

        # If a single commit exceeds target, truncate its diff to fit
        if tokens > target_tokens:
            print(f"  Truncating oversized commit {commit.hash[:10]} ({tokens:,} tokens)", file=sys.stderr)
            # Reserve some tokens for the header
            header = f"--- Commit {commit.hash[:10]} ({commit.date}) ---\n{commit.subject}\n\n"
            header_tokens = count_tokens(header)
            truncated_diff = _truncate_diff(diff, target_tokens - header_tokens - 200)

            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = []
                current_tokens = 0
            chunks.append([(commit, truncated_diff)])
            continue

        # Would this commit push us over?
        if current_tokens + tokens > target_tokens and current_chunk:
            chunks.append(current_chunk)
            current_chunk = []
            current_tokens = 0

        current_chunk.append((commit, diff))
        current_tokens += tokens

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def format_chunk(chunk: list[tuple[CommitInfo, str]]) -> str:
    """Render a chunk into a single string for the LLM."""
    parts = []
    for commit, diff in chunk:
        parts.append(format_commit(commit, diff))
    return "\n".join(parts)

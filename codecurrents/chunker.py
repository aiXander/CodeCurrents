"""Token counting and commit chunking."""

import re
import sys
from typing import Callable

from .git_ops import CommitInfo


# cl100k_base undercounts for non-OpenAI tokenizers. Apply a per-family margin
# instead of one global value so cheaper models don't waste their context budget.
_DEFAULT_MARGIN = 1.2
_MARGINS_BY_PREFIX = {
    # Exact (or near-exact) for OpenAI gpt/o* models — cl100k_base is theirs.
    "gpt": 1.0,
    "o1": 1.0,
    "o3": 1.0,
    "o4": 1.0,
    # Anthropic: empirically ~1.15-1.2x cl100k undercount.
    "claude": 1.2,
    "anthropic": 1.2,
    # Gemini: undercounts more, especially on code-heavy input.
    "gemini": 1.3,
    "google": 1.3,
}


def _margin_for_model(model: str) -> float:
    """Pick a safety margin for cl100k_base based on the target model family."""
    if not model:
        return _DEFAULT_MARGIN
    # OpenRouter form: "provider/model". Try both halves.
    candidates = [model.lower()]
    if "/" in model:
        provider, _, name = model.partition("/")
        candidates.extend([provider.lower(), name.lower()])
    for c in candidates:
        for prefix, margin in _MARGINS_BY_PREFIX.items():
            if c.startswith(prefix):
                return margin
    return _DEFAULT_MARGIN


try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")

    def _encode(text: str) -> list[int]:
        return _enc.encode(text, disallowed_special=())

    def _decode(tokens: list[int]) -> str:
        return _enc.decode(tokens)

    _HAS_TIKTOKEN = True
except Exception:
    _HAS_TIKTOKEN = False

    def _encode(text: str) -> list[int]:
        # Fallback: pretend each ~3 chars is one token. Doesn't support decode.
        return [0] * (len(text) // 3)

    def _decode(tokens: list[int]) -> str:
        raise RuntimeError("decode unavailable without tiktoken")


TokenCounter = Callable[[str], int]


def make_token_counter(model: str) -> TokenCounter:
    """Return a token-counting function calibrated for `model`'s tokenizer family."""
    margin = _margin_for_model(model)

    def count(text: str) -> int:
        return int(len(_encode(text)) * margin)

    return count


# Default counter used when no model is provided (e.g. for ad-hoc imports/tests).
count_tokens: TokenCounter = make_token_counter("")


def _extract_file_list(diff: str) -> list[str]:
    return re.findall(r"^diff --git a/.+? b/(.+?)$", diff, re.MULTILINE)


def _split_per_file(diff: str) -> list[tuple[str, str]]:
    """Split a unified diff into (filepath, section) pairs."""
    sections = re.split(r"(?=^diff --git )", diff, flags=re.MULTILINE)
    out: list[tuple[str, str]] = []
    for section in sections:
        if not section.strip():
            continue
        m = re.match(r"diff --git a/(.+?) b/(.+?)$", section, re.MULTILINE)
        if not m:
            continue
        out.append((m.group(2), section))
    return out


# Path fragments we consider lower-signal when packing a truncated diff.
_LOW_SIGNAL_PATH_HINTS = (
    "/test/", "/tests/", "_test.", ".test.", ".spec.",
    "/__tests__/", "/snapshots/", "/__snapshots__/",
    "/vendor/", "/node_modules/", "/dist/", "/build/",
    "/generated/", ".generated.", "_generated.",
    "/fixtures/", "/__fixtures__/", "/migrations/",
)


def _file_signal_rank(path: str, section: str, counter: TokenCounter) -> tuple[int, int]:
    """Lower tuple = higher signal. Sort key for keeping the most informative files."""
    low_signal = any(h in path for h in _LOW_SIGNAL_PATH_HINTS)
    size = counter(section)
    # Prefer high-signal files; within each tier, prefer smaller (more files fit).
    return (1 if low_signal else 0, size)


def _truncate_diff_smart(
    diff: str,
    max_tokens: int,
    counter: TokenCounter,
) -> str:
    """Fit a diff into `max_tokens` by greedily packing files by signal rank.

    Drops lowest-signal/largest files first. If even the highest-signal file
    overflows by itself, that single file is token-truncated.
    """
    file_sections = _split_per_file(diff)
    if not file_sections:
        # Nothing parseable — fall back to whole-diff token truncation.
        return _truncate_text_to_tokens(diff, max_tokens)

    ranked = sorted(file_sections, key=lambda fs: _file_signal_rank(fs[0], fs[1], counter))

    kept: list[tuple[str, str]] = []
    dropped: list[str] = []
    used = 0
    for path, section in ranked:
        cost = counter(section) + 1  # +1 for the join newline
        if used + cost <= max_tokens:
            kept.append((path, section))
            used += cost
        else:
            dropped.append(path)

    if not kept:
        # Even the smallest highest-signal file doesn't fit — keep it, truncated.
        path, section = ranked[0]
        truncated = _truncate_text_to_tokens(section, max_tokens - 100)
        kept = [(path, truncated)]
        dropped = [p for p, _ in ranked[1:]]

    # Preserve original file order in the diff output.
    original_order = {p: i for i, (p, _) in enumerate(file_sections)}
    kept.sort(key=lambda fs: original_order.get(fs[0], 0))

    body = "\n".join(s for _, s in kept)
    if dropped:
        body += f"\n\n... [TRUNCATED {len(dropped)} files: {', '.join(dropped[:20])}{'...' if len(dropped) > 20 else ''}]"
    return body


def _truncate_text_to_tokens(text: str, max_tokens: int) -> str:
    """Encode once, slice the token list, decode back. O(N) not O(N log N)."""
    if max_tokens <= 0:
        return ""
    if not _HAS_TIKTOKEN:
        # Char-based fallback proportional to the ~3 chars/token estimate.
        approx = max(0, max_tokens * 3)
        return text[:approx] + ("\n... [TRUNCATED]" if len(text) > approx else "")
    tokens = _encode(text)
    if len(tokens) <= max_tokens:
        return text
    truncated = _decode(tokens[:max_tokens])
    return truncated + "\n... [TRUNCATED]"


def _format_authors(commit: CommitInfo) -> str:
    parts = [commit.author] if commit.author else []
    if commit.co_authors:
        parts.append("co-authored with: " + ", ".join(commit.co_authors))
    return " · ".join(parts)


def format_commit(commit: CommitInfo, diff: str) -> str:
    """Format a single commit + diff for LLM consumption."""
    authors = _format_authors(commit)
    author_line = f"\nauthor: {authors}" if authors else ""
    return f"--- Commit {commit.hash[:10]} ({commit.date}) ---{author_line}\n{commit.subject}\n\n{diff}\n"


def _chunk_header(chunk: list[tuple[CommitInfo, str]]) -> str:
    """A short preamble passed to the LLM so it can anchor the batch in time."""
    if not chunk:
        return ""
    first = chunk[0][0]
    last = chunk[-1][0]
    if first.date == last.date:
        span = first.date
    else:
        span = f"{first.date}  →  {last.date}"
    return f"# Batch of {len(chunk)} commits ({span})\n\n"


def chunk_commits(
    commits_with_diffs: list[tuple[CommitInfo, str]],
    target_tokens: int,
    counter: TokenCounter | None = None,
) -> list[list[tuple[CommitInfo, str]]]:
    """Group commits into chunks of roughly target_tokens each."""
    if not commits_with_diffs:
        return []

    count = counter or count_tokens
    chunks: list[list[tuple[CommitInfo, str]]] = []
    current_chunk: list[tuple[CommitInfo, str]] = []
    current_tokens = 0

    for commit, diff in commits_with_diffs:
        formatted = format_commit(commit, diff)
        tokens = count(formatted)

        if tokens > target_tokens:
            print(f"  Truncating oversized commit {commit.hash[:10]} ({tokens:,} tokens)", file=sys.stderr)
            header = f"--- Commit {commit.hash[:10]} ({commit.date}) ---\n{commit.subject}\n\n"
            header_tokens = count(header)
            truncated_diff = _truncate_diff_smart(diff, target_tokens - header_tokens - 200, count)

            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = []
                current_tokens = 0
            chunks.append([(commit, truncated_diff)])
            continue

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
    """Render a chunk into a single string for the LLM, with a timestamp-range header."""
    parts = [_chunk_header(chunk)]
    for commit, diff in chunk:
        parts.append(format_commit(commit, diff))
    return "\n".join(parts)

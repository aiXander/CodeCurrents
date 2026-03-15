"""Diff filtering — drop low-signal files and commits."""

import re
from fnmatch import fnmatch

from .config import LOW_SIGNAL_PATTERNS, MAX_DIFF_LINES_PER_FILE


def is_low_signal_commit(subject: str) -> bool:
    """Check if a commit message indicates a low-signal change."""
    for pattern in LOW_SIGNAL_PATTERNS:
        if re.match(pattern, subject, re.IGNORECASE):
            return True
    return False


def _matches_any_pattern(filepath: str, patterns: list[str]) -> bool:
    """Check if a filepath matches any of the ignore patterns."""
    for pattern in patterns:
        if fnmatch(filepath, pattern):
            return True
        # Also match against just the filename
        if "/" in filepath and fnmatch(filepath.split("/")[-1], pattern):
            return True
    return False


def filter_diff(diff_text: str, ignore_patterns: list[str]) -> str:
    """Filter a unified diff, removing hunks for ignored files and truncating large diffs."""
    if not diff_text.strip():
        return ""

    # Split into per-file sections
    file_diffs = re.split(r"(?=^diff --git )", diff_text, flags=re.MULTILINE)

    kept = []
    for section in file_diffs:
        if not section.strip():
            continue

        # Extract the file path from the diff header
        header_match = re.match(r"diff --git a/(.+?) b/(.+?)$", section, re.MULTILINE)
        if not header_match:
            continue

        filepath = header_match.group(2)

        # Skip ignored files
        if _matches_any_pattern(filepath, ignore_patterns):
            continue

        # Skip binary files
        if "Binary files" in section and "differ" in section:
            continue

        # Truncate very large single-file diffs
        lines = section.splitlines()
        if len(lines) > MAX_DIFF_LINES_PER_FILE:
            # Keep the header + first N lines, then add a truncation notice
            truncated = lines[:MAX_DIFF_LINES_PER_FILE]
            truncated.append(f"\n... [TRUNCATED: {len(lines) - MAX_DIFF_LINES_PER_FILE} more lines in {filepath}]")
            kept.append("\n".join(truncated))
        else:
            kept.append(section)

    return "\n".join(kept)

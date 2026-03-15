"""Persist LLM map and reduce summaries to per-repo Markdown files."""

import re
from datetime import datetime, timezone
from pathlib import Path

from .git_ops import CommitInfo


def _repo_filename(repo_url: str) -> str:
    """Convert a repo URL/path into a safe filename."""
    # Strip trailing slashes and .git
    name = repo_url.rstrip("/")
    name = re.sub(r"\.git$", "", name)
    # Take last two path segments (org/repo)
    parts = re.split(r"[/:]", name)
    slug = "_".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
    # Sanitize
    slug = re.sub(r"[^\w\-]", "_", slug)
    return f"{slug}.md"


def save_summaries(
    output_dir: str,
    repo_url: str,
    model: str,
    commits: list[CommitInfo],
    chunks: list[list[tuple[CommitInfo, str]]],
    map_summaries: list[str],
    reduce_summary: str,
) -> Path:
    """Append a run's map and reduce summaries to the repo's Markdown log.

    Returns the path to the summary file.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    filepath = out / _repo_filename(repo_url)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    first_hash = commits[0].hash if commits else "unknown"
    last_hash = commits[-1].hash if commits else "unknown"
    first_date = commits[0].date if commits else "?"
    last_date = commits[-1].date if commits else "?"

    lines: list[str] = []
    lines.append(f"# Run: {now}\n")
    lines.append(f"- **Model:** {model}")
    lines.append(f"- **Commits:** {len(commits)} ({first_hash[:10]}..{last_hash[:10]})")
    lines.append(f"- **Date range:** {first_date} to {last_date}")
    lines.append(f"- **Chunks:** {len(chunks)}")
    lines.append("")

    # Map summaries
    for i, summary in enumerate(map_summaries):
        chunk = chunks[i] if i < len(chunks) else []
        chunk_hashes = [c.hash[:10] for c, _ in chunk]
        chunk_dates = [c.date for c, _ in chunk]

        lines.append(f"## Map {i + 1}/{len(map_summaries)}")
        if chunk_hashes:
            lines.append(f"- **Commits:** {chunk_hashes[0]}..{chunk_hashes[-1]} ({len(chunk_hashes)} commits)")
            lines.append(f"- **Date range:** {chunk_dates[0]} to {chunk_dates[-1]}")
        lines.append("")
        lines.append(summary.strip())
        lines.append("")

    # Reduce summary
    lines.append("## Final Summary (Reduce)")
    lines.append("")
    lines.append(reduce_summary.strip())
    lines.append("")
    lines.append("---\n")

    with open(filepath, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return filepath

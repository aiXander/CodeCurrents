"""State management — tracks last processed commit per repo."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path


def repo_name_from_url(repo_url: str) -> str:
    """Derive a filesystem-safe name from a repo URL or path."""
    # Strip trailing .git and slashes
    clean = repo_url.rstrip("/").removesuffix(".git")
    # Extract org/repo from URL or use last two path components
    match = re.search(r"([^/:]+)/([^/:]+)$", clean)
    if match:
        return f"{match.group(1)}_{match.group(2)}"
    # Fallback: use the last path component
    return Path(clean).name


def load_state(state_dir: str, repo_url: str) -> dict:
    """Load state for a repo. Returns empty dict on first run."""
    path = Path(state_dir) / f"{repo_name_from_url(repo_url)}.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_state(state_dir: str, repo_url: str, last_commit: str, branch: str = "main") -> None:
    """Save state after a successful run."""
    path = Path(state_dir) / f"{repo_name_from_url(repo_url)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    state = {}
    if path.exists():
        state = json.loads(path.read_text())

    state.update({
        "last_commit": last_commit,
        "branch": branch,
        "last_run": datetime.now(timezone.utc).isoformat(),
    })

    path.write_text(json.dumps(state, indent=2) + "\n")

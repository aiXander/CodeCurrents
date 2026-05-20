"""Disk-backed cache of map-pass summaries, keyed by chunk content.

Lets a run survive a crash mid-reduce (or ctrl-C) without re-paying for already
completed map calls. Keyed by a hash of the formatted chunk text plus the model
and prompt, so changing any of those invalidates the entry.
"""

import hashlib
import json
import re
from pathlib import Path
from typing import Iterable

from .git_ops import CommitInfo
from .state import repo_name_from_url


def _safe(name: str) -> str:
    return re.sub(r"[^\w\-]", "_", name)


def chunk_key(chunk_text: str, model: str, prompt: str) -> str:
    """Stable content hash for a chunk + the model/prompt it would be summarized with."""
    h = hashlib.sha256()
    h.update(model.encode())
    h.update(b"\x00")
    h.update(prompt.encode())
    h.update(b"\x00")
    h.update(chunk_text.encode())
    return h.hexdigest()[:16]


def _repo_dir(cache_dir: str, repo_url: str) -> Path:
    return Path(cache_dir) / _safe(repo_name_from_url(repo_url)) / "chunks"


def load_cached(cache_dir: str, repo_url: str, key: str) -> str | None:
    path = _repo_dir(cache_dir, repo_url) / f"{key}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())["summary"]
    except Exception:
        return None


def save_cached(
    cache_dir: str,
    repo_url: str,
    key: str,
    summary: str,
    commits: Iterable[CommitInfo],
) -> None:
    dir_ = _repo_dir(cache_dir, repo_url)
    dir_.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": summary,
        "commits": [c.hash for c in commits],
    }
    (dir_ / f"{key}.json").write_text(json.dumps(payload) + "\n")

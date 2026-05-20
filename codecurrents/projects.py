"""Per-project storage layout.

Each tracked repo gets its own folder under PROJECTS_ROOT/<slug>/:

    config.json     — per-project CodeCurrentsConfig overrides
    state.json      — last analyzed commit, last run timestamp, branch
    chunks/         — disk-backed map-pass cache (same format as before)
    runs/<run_id>/  — one folder per analysis run
        meta.json   — run metadata (model, commits, chunk count, date range)
        map_<N>.md  — individual chunk summary (or FAILED placeholder)
        reduce.md   — final synthesised summary

The slug is derived from the repo URL via state.repo_name_from_url, so all of
the existing helpers (state, chunk_cache, summaries) can be pointed at this
layout by passing `state_dir = projects/<slug>` and `summaries_dir = projects/<slug>`.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import (
    CodeCurrentsConfig,
    DEFAULT_IGNORE_PATTERNS,
    DEFAULT_MAP_PROMPT,
    DEFAULT_REDUCE_PROMPT,
    DEFAULT_TOKENS_PER_CHUNK,
    DEFAULT_MAX_CONCURRENT_REQUESTS,
    MAX_COMMITS_DEFAULT,
)
from .state import repo_name_from_url
from .git_ops import CommitInfo, normalize_repo_url


PROJECTS_ROOT = Path("projects")


def slug_from_url(url: str) -> str:
    return repo_name_from_url(normalize_repo_url(url))


def project_root(slug: str, root: Path | None = None) -> Path:
    base = root if root is not None else PROJECTS_ROOT
    return base / slug


def list_project_slugs(root: Path | None = None) -> list[str]:
    base = root if root is not None else PROJECTS_ROOT
    if not base.exists():
        return []
    out: list[str] = []
    for entry in sorted(base.iterdir()):
        if entry.is_dir() and (entry / "config.json").exists():
            out.append(entry.name)
    return out


def default_project_config(repo_url: str) -> dict[str, Any]:
    """Sensible defaults for a freshly tracked project.

    The frontend will copy values from this dict into the form when the user
    clicks "Track new project". Edits round-trip back via save_project_config.
    Note: `since` is intentionally short — a long cold-start window on a
    blobless clone fetches a lot of blobs over HTTP and feels broken.
    """
    return {
        "repo_url": repo_url,
        "branches": ["main"],
        "since": "7 days ago",
        "model": "anthropic/claude-haiku-4-5",
        "tokens_per_chunk": DEFAULT_TOKENS_PER_CHUNK,
        "max_commits": MAX_COMMITS_DEFAULT,
        "max_concurrent_requests": DEFAULT_MAX_CONCURRENT_REQUESTS,
        "clone_dir": "/tmp/codecurrents_repos",
        "ignore_patterns": list(DEFAULT_IGNORE_PATTERNS),
        "map_prompt": DEFAULT_MAP_PROMPT,
        "reduce_prompt": DEFAULT_REDUCE_PROMPT,
    }


def load_root_config_overrides() -> dict[str, Any]:
    """Read repo-root ./config.json (if any) and return the keys we honour as defaults.

    The CLI used to treat root-level config.json as the source of truth; in the
    GUI world we treat it as 'factory defaults' for new projects.
    """
    root_cfg = Path("config.json")
    if not root_cfg.exists():
        return {}
    try:
        raw = json.loads(root_cfg.read_text())
    except Exception:
        return {}
    # `since` and `branches` are intentionally NOT inherited from the root
    # config: they're per-project decisions and we don't want a new repo to
    # silently pick up the global CLI default (e.g. "21 days ago", which can
    # mean a 10-minute git fetch on a busy repo).
    allowed = {
        "model", "tokens_per_chunk", "max_commits",
        "max_concurrent_requests", "clone_dir", "ignore_patterns",
        "map_prompt", "reduce_prompt",
    }
    return {k: v for k, v in raw.items() if k in allowed}


def create_project(
    repo_url: str,
    root: Path | None = None,
    *,
    overrides: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Create a new project folder and write defaults to its config.json.

    Returns (slug, config_dict). If the project already exists, this is a no-op
    and the existing config is returned unchanged.
    """
    norm = normalize_repo_url(repo_url)
    slug = repo_name_from_url(norm)
    pdir = project_root(slug, root)
    pdir.mkdir(parents=True, exist_ok=True)

    cfg_path = pdir / "config.json"
    if cfg_path.exists():
        existing = json.loads(cfg_path.read_text())
        # Ensure repo_url field present even on older configs.
        if "repo_url" not in existing:
            existing["repo_url"] = norm
            cfg_path.write_text(json.dumps(existing, indent=2) + "\n")
        return slug, existing

    cfg = default_project_config(norm)
    cfg.update(load_root_config_overrides())
    cfg["repo_url"] = norm  # never let overrides clobber the URL
    if overrides:
        cfg.update(overrides)

    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")
    (pdir / "runs").mkdir(exist_ok=True)
    return slug, cfg


def load_project_config(slug: str, root: Path | None = None) -> dict[str, Any]:
    cfg_path = project_root(slug, root) / "config.json"
    if not cfg_path.exists():
        raise FileNotFoundError(f"No project at {cfg_path}")
    return json.loads(cfg_path.read_text())


def save_project_config(slug: str, cfg: dict[str, Any], root: Path | None = None) -> None:
    cfg_path = project_root(slug, root) / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")


def state_file_path(slug: str, root: Path | None = None) -> Path:
    """Path where `state.save_state` writes when state_dir = projects/<slug>.

    state.save_state composes `<state_dir>/<repo_name>.json`, so with state_dir
    pointed at the project folder we get `projects/<slug>/<slug>.json`.
    """
    return project_root(slug, root) / f"{slug}.json"


def load_project_state(slug: str, root: Path | None = None) -> dict[str, Any]:
    sp = state_file_path(slug, root)
    if not sp.exists():
        return {}
    try:
        return json.loads(sp.read_text())
    except Exception:
        return {}


def delete_project(slug: str, root: Path | None = None) -> None:
    pdir = project_root(slug, root)
    if pdir.exists():
        shutil.rmtree(pdir)


def project_config_to_runtime(
    cfg: dict[str, Any],
    slug: str,
    root: Path | None = None,
    *,
    reprocess: bool = False,
) -> CodeCurrentsConfig:
    """Materialise a CodeCurrentsConfig that writes into the per-project folder.

    Important: state_dir and summaries_dir both point at the project folder, so
    state.json lands at projects/<slug>/state.json and chunk cache lands at
    projects/<slug>/<slug>/chunks/... (the existing helpers nest one extra
    layer; this is fine because we never look at it directly).
    """
    pdir = project_root(slug, root)
    return CodeCurrentsConfig(
        repo_url=cfg["repo_url"],
        branches=cfg.get("branches", ["main"]),
        since=cfg.get("since"),
        start_commit=cfg.get("start_commit"),
        end_commit=cfg.get("end_commit", "HEAD"),
        model=cfg.get("model", "anthropic/claude-haiku-4-5"),
        map_prompt=cfg.get("map_prompt", DEFAULT_MAP_PROMPT),
        reduce_prompt=cfg.get("reduce_prompt", DEFAULT_REDUCE_PROMPT),
        ignore_patterns=list(cfg.get("ignore_patterns", DEFAULT_IGNORE_PATTERNS)),
        tokens_per_chunk=int(cfg.get("tokens_per_chunk", DEFAULT_TOKENS_PER_CHUNK)),
        max_commits=int(cfg.get("max_commits", MAX_COMMITS_DEFAULT)),
        max_concurrent_requests=int(cfg.get("max_concurrent_requests", DEFAULT_MAX_CONCURRENT_REQUESTS)),
        state_dir=str(pdir),
        summaries_dir=str(pdir / "summaries-legacy"),  # legacy md log; ignored by GUI
        clone_dir=cfg.get("clone_dir", "/tmp/codecurrents_repos"),
        reprocess=reprocess,
    )


# ---------------------------------------------------------------------------
# Runs — structured per-analysis output ready for the frontend
# ---------------------------------------------------------------------------

def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def runs_dir(slug: str, root: Path | None = None) -> Path:
    d = project_root(slug, root) / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_runs(slug: str, root: Path | None = None) -> list[dict[str, Any]]:
    """List runs newest-first with their meta.json contents."""
    d = runs_dir(slug, root)
    out: list[dict[str, Any]] = []
    for entry in sorted(d.iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        meta_path = entry / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text())
        except Exception:
            continue
        meta["run_id"] = entry.name
        out.append(meta)
    return out


def get_run(slug: str, run_id: str, root: Path | None = None) -> dict[str, Any] | None:
    run_dir = runs_dir(slug, root) / run_id
    if not run_dir.exists():
        return None
    meta_path = run_dir / "meta.json"
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text())

    maps: list[dict[str, Any]] = []
    for chunk_meta in meta.get("chunks", []):
        idx = chunk_meta["index"]
        map_file = run_dir / f"map_{idx + 1}.md"
        summary = map_file.read_text() if map_file.exists() else None
        maps.append({**chunk_meta, "summary": summary})

    reduce_path = run_dir / "reduce.md"
    reduce_summary = reduce_path.read_text() if reduce_path.exists() else None

    return {
        "run_id": run_id,
        "meta": meta,
        "maps": maps,
        "reduce_summary": reduce_summary,
    }


def save_run(
    slug: str,
    *,
    run_id: str,
    repo_url: str,
    model: str,
    branches: list[str],
    since: str | None,
    commits: list[CommitInfo],
    chunks: list[list[tuple[CommitInfo, str]]],
    map_summaries: list[str | None],
    reduce_summary: str,
    root: Path | None = None,
) -> Path:
    """Persist a structured run folder under projects/<slug>/runs/<run_id>/."""
    run_dir = runs_dir(slug, root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    chunk_meta: list[dict[str, Any]] = []
    for i, chunk in enumerate(chunks):
        commits_in_chunk = [
            {
                "hash": c.hash,
                "short": c.hash[:10],
                "date": c.date,
                "subject": c.subject,
                "author": c.author,
                "author_email": c.author_email,
                "co_authors": c.co_authors,
            }
            for c, _ in chunk
        ]
        chunk_meta.append({
            "index": i,
            "commit_count": len(chunk),
            "date_first": chunk[0][0].date if chunk else None,
            "date_last": chunk[-1][0].date if chunk else None,
            "commits": commits_in_chunk,
            "failed": map_summaries[i] is None,
        })

        # Write the summary file (or a placeholder on failure)
        out_path = run_dir / f"map_{i + 1}.md"
        if map_summaries[i] is None:
            out_path.write_text("*[FAILED — chunk exhausted all retries; see server log.]*\n")
        else:
            out_path.write_text(map_summaries[i].strip() + "\n")

    (run_dir / "reduce.md").write_text((reduce_summary or "").strip() + "\n")

    meta = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "repo_url": repo_url,
        "model": model,
        "branches": branches,
        "since": since,
        "commit_count": len(commits),
        "first_commit": {
            "hash": commits[0].hash,
            "short": commits[0].hash[:10],
            "date": commits[0].date,
        } if commits else None,
        "last_commit": {
            "hash": commits[-1].hash,
            "short": commits[-1].hash[:10],
            "date": commits[-1].date,
        } if commits else None,
        "chunk_count": len(chunks),
        "failed_chunks": sum(1 for s in map_summaries if s is None),
        "chunks": chunk_meta,
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")

    return run_dir


def delete_run(slug: str, run_id: str, root: Path | None = None) -> bool:
    rd = runs_dir(slug, root) / run_id
    if not rd.exists():
        return False
    shutil.rmtree(rd)
    return True


def github_web_url(repo_url: str) -> str | None:
    """Convert an arbitrary git URL into a https://github.com/<owner>/<repo> URL.

    Returns None if it doesn't look like a GitHub repo.
    """
    m = re.match(
        r"^(?:https?://|git@)([^/:]+)[/:]([^/]+)/([^/]+?)(?:\.git)?/?$",
        repo_url.strip(),
    )
    if not m:
        return None
    host, owner, repo = m.group(1), m.group(2), m.group(3)
    if "github" not in host:
        return None
    return f"https://github.com/{owner}/{repo}"

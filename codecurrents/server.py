"""FastAPI backend for the CodeCurrents GUI.

Run with:
    python -m codecurrents.server
or via the `codecurrents-server` console script defined in pyproject.toml.

Endpoints (all under /api):
    GET    /projects                          → list tracked projects + status snapshot
    POST   /projects                          → track a new repo (body: {"repo_url": "..."})
    GET    /projects/{slug}                   → full project payload (config, state, runs)
    PUT    /projects/{slug}/config            → save edited config
    DELETE /projects/{slug}                   → untrack (rm -rf project folder)
    GET    /projects/{slug}/github-status     → poll GitHub for ahead count + last push
    POST   /projects/{slug}/analyze           → kick off a background run; returns {run_id}
    GET    /projects/{slug}/runs              → list past runs (newest first)
    GET    /projects/{slug}/runs/{run_id}     → run detail (maps + reduce)
    GET    /projects/{slug}/stream            → Server-Sent Events log stream
    GET    /defaults                          → default config dict for new projects
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import threading
import time
import urllib.request
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import projects as projects_mod
from .config import (
    CodeCurrentsConfig,
    DEFAULT_IGNORE_PATTERNS,
    DEFAULT_MAP_PROMPT,
    DEFAULT_REDUCE_PROMPT,
    DEFAULT_TOKENS_PER_CHUNK,
    DEFAULT_MAX_CONCURRENT_REQUESTS,
    MAX_COMMITS_DEFAULT,
)
from .core import summarize_repo
from .git_ops import normalize_repo_url


# ---------------------------------------------------------------------------
# .env loading (mirrors cli._load_env_file so the server picks up API keys)
# ---------------------------------------------------------------------------
def _load_env_files() -> None:
    for env_path in [Path(".env"), Path.home() / ".eve", Path.home() / ".env"]:
        if not env_path.exists():
            continue
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if key not in os.environ:
                os.environ[key] = value


_load_env_files()


# ---------------------------------------------------------------------------
# In-process run registry — tracks live and finished runs so the SSE endpoint
# can replay logs and the UI can detect completion.
# ---------------------------------------------------------------------------
class RunRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: Dict[str, Dict[str, Any]] = {}  # run_id → {status, logs, project_slug, started_at, ...}

    def start(self, slug: str, run_id: str) -> None:
        with self._lock:
            self._runs[run_id] = {
                "run_id": run_id,
                "project_slug": slug,
                "status": "running",
                "logs": [],
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None,
                "error": None,
                "result": None,
            }

    def log(self, run_id: str, line: str) -> None:
        with self._lock:
            r = self._runs.get(run_id)
            if r is not None:
                r["logs"].append({"t": time.time(), "line": line})

    def finish(self, run_id: str, *, error: str | None = None, result: str | None = None) -> None:
        with self._lock:
            r = self._runs.get(run_id)
            if r is None:
                return
            r["status"] = "error" if error else "done"
            r["finished_at"] = datetime.now(timezone.utc).isoformat()
            r["error"] = error
            r["result"] = result

    def get(self, run_id: str) -> Dict[str, Any] | None:
        with self._lock:
            r = self._runs.get(run_id)
            return None if r is None else dict(r)

    def latest_for_project(self, slug: str) -> Dict[str, Any] | None:
        with self._lock:
            matches = [r for r in self._runs.values() if r["project_slug"] == slug]
            if not matches:
                return None
            return dict(max(matches, key=lambda r: r["started_at"]))

    def active(self, slug: str) -> Dict[str, Any] | None:
        with self._lock:
            for r in self._runs.values():
                if r["project_slug"] == slug and r["status"] == "running":
                    return dict(r)
            return None


REGISTRY = RunRegistry()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _project_payload(slug: str) -> dict[str, Any]:
    cfg = projects_mod.load_project_config(slug)
    state = projects_mod.load_project_state(slug)
    runs = projects_mod.list_runs(slug)
    active = REGISTRY.active(slug)
    return {
        "slug": slug,
        "config": cfg,
        "state": state,
        "runs": runs,
        "active_run": active,
        "github_web_url": projects_mod.github_web_url(cfg.get("repo_url", "")),
    }


def _github_api_path(repo_url: str) -> tuple[str, str] | None:
    """Return (owner, repo) for a github.com URL, else None."""
    import re
    m = re.match(
        r"^(?:https?://|git@)([^/:]+)[/:]([^/]+)/([^/]+?)(?:\.git)?/?$",
        repo_url.strip(),
    )
    if not m or "github" not in m.group(1):
        return None
    return m.group(2), m.group(3)


def _github_get(path: str) -> Any:
    """GET https://api.github.com/<path>. Uses GITHUB_TOKEN if set."""
    url = f"https://api.github.com/{path.lstrip('/')}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "codecurrents-gui")
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class TrackProjectRequest(BaseModel):
    repo_url: str


class AnalyzeRequest(BaseModel):
    reprocess: bool = False


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="CodeCurrents GUI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/defaults")
def get_defaults() -> dict[str, Any]:
    overrides = projects_mod.load_root_config_overrides()
    base = projects_mod.default_project_config("")
    base.update(overrides)
    base.pop("repo_url", None)
    return {
        "config": base,
        "ignore_patterns_default": list(DEFAULT_IGNORE_PATTERNS),
        "tokens_per_chunk_default": DEFAULT_TOKENS_PER_CHUNK,
        "max_commits_default": MAX_COMMITS_DEFAULT,
        "max_concurrent_default": DEFAULT_MAX_CONCURRENT_REQUESTS,
        "map_prompt_default": DEFAULT_MAP_PROMPT,
        "reduce_prompt_default": DEFAULT_REDUCE_PROMPT,
    }


@app.get("/api/projects")
def list_projects() -> dict[str, Any]:
    slugs = projects_mod.list_project_slugs()
    items = []
    for slug in slugs:
        try:
            cfg = projects_mod.load_project_config(slug)
        except Exception:
            continue
        state = projects_mod.load_project_state(slug)
        runs = projects_mod.list_runs(slug)
        active = REGISTRY.active(slug)
        items.append({
            "slug": slug,
            "repo_url": cfg.get("repo_url"),
            "branches": cfg.get("branches", []),
            "model": cfg.get("model"),
            "last_run": state.get("last_run"),
            "last_commit": state.get("last_commit"),
            "run_count": len(runs),
            "latest_run_id": runs[0]["run_id"] if runs else None,
            "active_run_id": active["run_id"] if active else None,
            "github_web_url": projects_mod.github_web_url(cfg.get("repo_url", "")),
        })
    return {"projects": items}


@app.post("/api/projects")
def create_project(req: TrackProjectRequest) -> dict[str, Any]:
    if not req.repo_url.strip():
        raise HTTPException(400, "repo_url is required")
    slug, cfg = projects_mod.create_project(req.repo_url)
    return _project_payload(slug)


@app.get("/api/projects/{slug}")
def get_project(slug: str) -> dict[str, Any]:
    if not (projects_mod.project_root(slug) / "config.json").exists():
        raise HTTPException(404, f"Project not found: {slug}")
    return _project_payload(slug)


@app.put("/api/projects/{slug}/config")
async def update_config(slug: str, req: Request) -> dict[str, Any]:
    if not (projects_mod.project_root(slug) / "config.json").exists():
        raise HTTPException(404, f"Project not found: {slug}")
    body = await req.json()
    existing = projects_mod.load_project_config(slug)
    # Disallow rewriting repo_url — that would orphan the slug.
    body.pop("slug", None)
    body.pop("state", None)
    new_cfg = {**existing, **body}
    new_cfg["repo_url"] = existing["repo_url"]
    projects_mod.save_project_config(slug, new_cfg)
    return _project_payload(slug)


@app.delete("/api/projects/{slug}")
def delete_project(slug: str) -> dict[str, str]:
    if not (projects_mod.project_root(slug) / "config.json").exists():
        raise HTTPException(404, f"Project not found: {slug}")
    projects_mod.delete_project(slug)
    return {"deleted": slug}


@app.get("/api/projects/{slug}/github-status")
def github_status(slug: str) -> dict[str, Any]:
    cfg = projects_mod.load_project_config(slug)
    state = projects_mod.load_project_state(slug)
    repo_url = cfg.get("repo_url", "")
    parsed = _github_api_path(repo_url)
    if not parsed:
        return {"available": False, "reason": "Not a github.com URL"}
    owner, repo = parsed
    branch = (cfg.get("branches") or ["main"])[0]

    out: dict[str, Any] = {
        "available": True,
        "branch": branch,
        "owner": owner,
        "repo": repo,
        "last_commit": state.get("last_commit"),
        "last_run": state.get("last_run"),
    }

    try:
        meta = _github_get(f"repos/{owner}/{repo}")
        out["github_pushed_at"] = meta.get("pushed_at")
        out["github_default_branch"] = meta.get("default_branch")
        out["github_stargazers"] = meta.get("stargazers_count")
        out["github_description"] = meta.get("description")
    except urllib.error.HTTPError as e:
        out["github_error"] = f"{e.code}: {e.reason}"
        return out
    except Exception as e:
        out["github_error"] = str(e)
        return out

    last = state.get("last_commit")
    if last:
        try:
            cmp_data = _github_get(f"repos/{owner}/{repo}/compare/{last}...{branch}")
            out["ahead_by"] = cmp_data.get("ahead_by")
            out["behind_by"] = cmp_data.get("behind_by")
            recent = []
            for c in (cmp_data.get("commits") or [])[-20:]:
                commit = c.get("commit", {})
                recent.append({
                    "sha": c.get("sha"),
                    "short": (c.get("sha") or "")[:10],
                    "message": (commit.get("message") or "").split("\n", 1)[0],
                    "author": (commit.get("author") or {}).get("name"),
                    "date": (commit.get("author") or {}).get("date"),
                    "html_url": c.get("html_url"),
                })
            out["new_commits"] = recent
        except urllib.error.HTTPError as e:
            out["compare_error"] = f"{e.code}: {e.reason}"
        except Exception as e:
            out["compare_error"] = str(e)
    else:
        try:
            branch_data = _github_get(f"repos/{owner}/{repo}/branches/{branch}")
            commit = branch_data.get("commit", {}).get("commit", {})
            out["head_commit"] = {
                "sha": branch_data.get("commit", {}).get("sha"),
                "message": (commit.get("message") or "").split("\n", 1)[0],
                "date": (commit.get("author") or {}).get("date"),
            }
        except Exception as e:
            out["head_error"] = str(e)

    return out


@app.get("/api/projects/{slug}/runs")
def list_project_runs(slug: str) -> dict[str, Any]:
    return {"runs": projects_mod.list_runs(slug)}


@app.get("/api/projects/{slug}/runs/{run_id}")
def get_project_run(slug: str, run_id: str) -> dict[str, Any]:
    data = projects_mod.get_run(slug, run_id)
    if data is None:
        raise HTTPException(404, f"Run not found: {run_id}")
    return data


@app.delete("/api/projects/{slug}/runs/{run_id}")
def delete_project_run(slug: str, run_id: str) -> dict[str, str]:
    if not projects_mod.delete_run(slug, run_id):
        raise HTTPException(404, f"Run not found: {run_id}")
    return {"deleted": run_id}


@app.get("/api/projects/{slug}/clone-status")
def clone_status(slug: str) -> dict[str, Any]:
    """Inspect the cached bare clone — exists, blobless?, on-disk size."""
    from .git_ops import is_blobless_clone
    cfg = projects_mod.load_project_config(slug)
    repo_url = cfg.get("repo_url", "")
    name = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
    bare_path = Path(cfg.get("clone_dir") or "/tmp/codecurrents_repos") / f"{name}.git"
    if not bare_path.exists():
        return {"exists": False, "path": str(bare_path)}
    try:
        blobless = is_blobless_clone(str(bare_path))
    except Exception:
        blobless = False
    # Cheap directory size (du-equivalent).
    total = 0
    for root, _, files in os.walk(bare_path):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return {
        "exists": True,
        "path": str(bare_path),
        "blobless": blobless,
        "size_bytes": total,
    }


@app.delete("/api/projects/{slug}/clone")
def delete_clone(slug: str) -> dict[str, Any]:
    """Delete the cached bare clone so the next analysis re-clones fresh.

    Useful when an old blobless clone is making diff walks slow — deleting it
    forces the next run to do a full bare clone (no filter by default).
    """
    import shutil
    cfg = projects_mod.load_project_config(slug)
    repo_url = cfg.get("repo_url", "")
    name = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
    bare_path = Path(cfg.get("clone_dir") or "/tmp/codecurrents_repos") / f"{name}.git"
    if not bare_path.exists():
        return {"deleted": False, "path": str(bare_path), "reason": "did not exist"}
    shutil.rmtree(bare_path)
    return {"deleted": True, "path": str(bare_path)}


@app.post("/api/projects/{slug}/analyze")
def analyze(slug: str, req: AnalyzeRequest) -> dict[str, Any]:
    if not (projects_mod.project_root(slug) / "config.json").exists():
        raise HTTPException(404, f"Project not found: {slug}")
    active = REGISTRY.active(slug)
    if active:
        raise HTTPException(409, f"A run is already in progress for {slug}: {active['run_id']}")

    run_id = projects_mod.new_run_id()
    REGISTRY.start(slug, run_id)

    cfg = projects_mod.load_project_config(slug)
    runtime_cfg = projects_mod.project_config_to_runtime(cfg, slug, reprocess=req.reprocess)
    runtime_cfg.project_slug = slug
    runtime_cfg.run_id = run_id

    def _runner() -> None:
        capture = _StreamingLogCapture(slug, run_id, REGISTRY)
        try:
            with redirect_stderr(capture), redirect_stdout(capture):
                result = summarize_repo(runtime_cfg)
            REGISTRY.finish(run_id, result=result)
        except Exception as e:
            REGISTRY.log(run_id, f"ERROR: {e}")
            REGISTRY.finish(run_id, error=str(e))

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    return {"run_id": run_id, "status": "running"}


@app.get("/api/projects/{slug}/runs/{run_id}/stream")
async def stream_run(slug: str, run_id: str) -> StreamingResponse:
    async def gen():
        idx = 0
        while True:
            r = REGISTRY.get(run_id)
            if r is None:
                yield f"event: error\ndata: {json.dumps({'message': 'unknown run'})}\n\n"
                return
            logs = r["logs"]
            while idx < len(logs):
                payload = json.dumps(logs[idx])
                yield f"data: {payload}\n\n"
                idx += 1
            if r["status"] != "running":
                done = {"status": r["status"], "error": r["error"], "result": r["result"]}
                yield f"event: done\ndata: {json.dumps(done)}\n\n"
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/projects/{slug}/runs/{run_id}/status")
def run_status(slug: str, run_id: str) -> dict[str, Any]:
    r = REGISTRY.get(run_id)
    if r is None:
        # If not in-memory, check disk
        disk = projects_mod.get_run(slug, run_id)
        if disk is None:
            raise HTTPException(404, f"Run not found: {run_id}")
        return {"status": "done", "logs": [], "on_disk": True}
    return {
        "status": r["status"],
        "logs": r["logs"],
        "error": r["error"],
        "result": r["result"],
        "started_at": r["started_at"],
        "finished_at": r["finished_at"],
    }


# ---------------------------------------------------------------------------
# Static frontend serving (optional — for single-binary local deployment)
# ---------------------------------------------------------------------------
_FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"
if _FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")


# ---------------------------------------------------------------------------
# Log capture — forwards each line into the RunRegistry for SSE replay.
# ---------------------------------------------------------------------------
class _StreamingLogCapture(io.TextIOBase):
    def __init__(self, slug: str, run_id: str, registry: RunRegistry) -> None:
        self._buf = ""
        self._registry = registry
        self._slug = slug
        self._run_id = run_id
        self._original_stderr = sys.__stderr__

    def write(self, s: str) -> int:
        # Also forward to the real stderr so the server console keeps working.
        try:
            self._original_stderr.write(s)
        except Exception:
            pass
        self._buf += s
        while "\n" in self._buf:
            line, _, self._buf = self._buf.partition("\n")
            line = line.rstrip()
            if line:
                self._registry.log(self._run_id, line)
        return len(s)

    def flush(self) -> None:
        if self._buf.strip():
            self._registry.log(self._run_id, self._buf.rstrip())
            self._buf = ""


def main() -> None:
    import uvicorn
    port = int(os.environ.get("CODECURRENTS_PORT", "8765"))
    host = os.environ.get("CODECURRENTS_HOST", "127.0.0.1")
    print(f"CodeCurrents server starting on http://{host}:{port}", file=sys.stderr)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()

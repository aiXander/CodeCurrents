"""Git operations via subprocess."""

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CommitInfo:
    hash: str
    date: str
    subject: str


def _run_git(args: list[str], cwd: str | None = None) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])}... failed: {result.stderr.strip()}")
    return result.stdout


def ensure_repo(repo_url: str, clone_dir: str) -> str:
    """Clone or fetch a repo. Returns path to the bare repo."""
    # If it's a local path, use it directly
    local = Path(repo_url)
    if local.is_dir() and (local / ".git").exists():
        return str(local)
    if local.is_dir() and (local / "HEAD").exists():
        # Already a bare repo
        return str(local)

    # Derive a directory name from the URL
    name = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
    bare_path = Path(clone_dir) / f"{name}.git"

    if bare_path.exists():
        _run_git(["fetch", "--all", "--prune"], cwd=str(bare_path))
        return str(bare_path)

    bare_path.parent.mkdir(parents=True, exist_ok=True)
    _run_git(["clone", "--bare", repo_url, str(bare_path)])
    return str(bare_path)


def get_commits(
    repo_path: str,
    branch: str,
    start_commit: str | None,
    end_commit: str = "HEAD",
    max_commits: int = 500,
    since: str | None = None,
) -> list[CommitInfo]:
    """Get commits in range, by date, or the last N. Supports git --since syntax."""
    sep = "||"
    fmt = f"%H{sep}%aI{sep}%s"

    base_args = ["log", f"--format={fmt}", "--no-merges", f"-n{max_commits}"]
    if since:
        base_args.append(f"--since={since}")

    if start_commit:
        range_spec = f"{start_commit}..{end_commit}"
        args = base_args + [range_spec, "--", branch]
        try:
            output = _run_git(args, cwd=repo_path)
        except RuntimeError:
            range_spec = f"{start_commit}..refs/heads/{branch}"
            args = base_args + [range_spec]
            output = _run_git(args, cwd=repo_path)
    else:
        try:
            args = base_args + [branch]
            output = _run_git(args, cwd=repo_path)
        except RuntimeError:
            args = base_args + [f"refs/heads/{branch}"]
            output = _run_git(args, cwd=repo_path)

    commits = []
    for line in output.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split(sep, 2)
        if len(parts) == 3:
            commits.append(CommitInfo(hash=parts[0], date=parts[1], subject=parts[2]))

    # Return in chronological order (oldest first)
    commits.reverse()
    return commits


def get_diff(repo_path: str, commit_hash: str) -> str:
    """Get the full diff for a single commit."""
    try:
        return _run_git(["show", "--format=", "--patch", commit_hash], cwd=repo_path)
    except RuntimeError:
        # Fallback for edge cases
        return _run_git(["diff-tree", "-p", commit_hash], cwd=repo_path)


def get_diff_stat(repo_path: str, commit_hash: str) -> str:
    """Get the diffstat (file list + line counts) for a commit."""
    return _run_git(["show", "--format=", "--stat", commit_hash], cwd=repo_path)

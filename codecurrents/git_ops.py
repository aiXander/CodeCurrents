"""Git operations via subprocess."""

import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path


# Header format used by `git log -p` to interleave commit metadata with diffs in
# a single subprocess call. Pick markers unlikely to appear in commit messages.
_COMMIT_MARKER = "\x1eCC_COMMIT\x1e"
_FIELD_SEP = "\x1fCC\x1f"
_LOG_FORMAT = (
    f"{_COMMIT_MARKER}%H{_FIELD_SEP}%aI{_FIELD_SEP}%an{_FIELD_SEP}%ae"
    f"{_FIELD_SEP}%(trailers:key=Co-authored-by,valueonly,separator=|){_FIELD_SEP}%s"
)

# Default timeout for short git ops (log, show). Fetch/clone can be far slower
# on large repos, so they get a longer timeout (see _SLOW_GIT_TIMEOUT).
_GIT_TIMEOUT = 120
_SLOW_GIT_TIMEOUT = 300  # fetch/clone


def normalize_repo_url(repo_url: str) -> str:
    """Return a canonical form of a repo URL for state keying and filename generation.

    Strips trailing slashes and .git, resolves local paths to absolute, and
    converts SSH URLs (git@host:org/repo) to HTTPS so different URL formats
    for the same repo share the same state file.
    """
    url = repo_url.strip().rstrip("/").removesuffix(".git")

    local = Path(url)
    if local.exists():
        return str(local.resolve())

    ssh_match = re.match(r"git@([^:]+):(.+)$", url)
    if ssh_match:
        host, path = ssh_match.group(1), ssh_match.group(2)
        return f"https://{host}/{path}"

    return url


@dataclass
class CommitInfo:
    hash: str
    date: str
    subject: str
    author: str = ""
    author_email: str = ""
    co_authors: list[str] = field(default_factory=list)


def _run_git(args: list[str], cwd: str | None = None, timeout: int = _GIT_TIMEOUT) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        # Replace undecodable bytes instead of crashing the whole batch on one
        # non-UTF-8 file. We lose fidelity in the offending diff lines but the
        # rest of the run still completes.
        errors="replace",
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])}... failed: {result.stderr.strip()}")
    return result.stdout


def _run_git_streaming(
    args: list[str],
    cwd: str | None = None,
    timeout: int = _SLOW_GIT_TIMEOUT,
    log_prefix: str = "  git",
) -> None:
    """Run a long git command, streaming its stderr live to sys.stderr.

    Used for clone/fetch where the user needs to see progress (otherwise the
    UI looks frozen). git emits progress on stderr in carriage-return-terminated
    lines like "Receiving objects:  60% (320/532)". We collapse repeated
    progress updates so the SSE log box doesn't drown in 1000 lines of
    "Receiving objects: 1%, 2%, …" — only print when the message changes
    materially (different stage, or +5% on the current one).
    """
    proc = subprocess.Popen(
        ["git", "-c", "advice.detachedHead=false"] + args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        bufsize=1,
    )

    deadline = time.monotonic() + timeout
    last_printed: dict[str, int] = {}  # stage → last percent printed
    last_other: str | None = None
    captured_err: list[str] = []

    assert proc.stderr is not None
    # Iterate over a custom byte-by-byte reader so we can handle git's '\r'
    # progress updates (text-mode line iteration would buffer until '\n').
    buf = ""
    while True:
        if time.monotonic() > deadline:
            proc.kill()
            raise RuntimeError(f"git {' '.join(args[:3])}... timed out after {timeout}s")

        ch = proc.stderr.read(1)
        if not ch:
            # Stream closed; check if process exited.
            if proc.poll() is not None:
                break
            time.sleep(0.05)
            continue

        if ch in ("\r", "\n"):
            line = buf.strip()
            buf = ""
            if not line:
                continue
            captured_err.append(line)

            # Collapse percent updates: only print at material milestones.
            # git emits two prefix shapes:
            #   "remote: Counting objects:   3% (36/1173)"
            #   "Receiving objects:  12% (140/1173)"
            normalised = re.sub(r"^remote:\s*", "", line)
            m = re.match(r"^([A-Za-z][\w /]+?):\s+(\d+)%", normalised)
            if m:
                stage, pct = m.group(1), int(m.group(2))
                prev = last_printed.get(stage, -10)
                if pct == 100 or pct - prev >= 10:
                    print(f"{log_prefix} {line}", file=sys.stderr, flush=True)
                    last_printed[stage] = pct
                continue
            # Non-progress line. Only print if it isn't a repeat.
            if line != last_other:
                print(f"{log_prefix} {line}", file=sys.stderr, flush=True)
                last_other = line
        else:
            buf += ch

    if buf.strip():
        print(f"{log_prefix} {buf.strip()}", file=sys.stderr, flush=True)

    rc = proc.wait()
    if rc != 0:
        tail = "\n".join(captured_err[-10:]) or "(no stderr)"
        raise RuntimeError(f"git {' '.join(args[:3])}... failed (rc={rc}): {tail}")


def ensure_repo(
    repo_url: str,
    clone_dir: str,
    single_branch: str | None = None,
    clone_filter: str | None = None,
    shallow_since: str | None = None,
) -> str:
    """Clone or fetch a repo. Returns path to the bare repo.

    If `single_branch` is given, the clone is restricted to that one ref.

    `clone_filter` controls git's partial-clone filter (e.g. "blob:none" for
    a blobless clone). When None (the default) we do a full bare clone — git
    log -p is then purely local and fast. Blobless saves disk + initial
    bandwidth but every diff walk pays HTTP round-trips for missing blobs.

    `shallow_since` (e.g. "7 days ago") creates / deepens to a shallow clone
    bounded at that date. This is the biggest first-clone win for active
    repos: instead of pulling years of history, git only fetches the commits
    inside the analysis window plus their immediate parents. Subsequent runs
    with a wider window call `git fetch --shallow-since=<earlier>` to deepen
    in-place.
    """
    local = Path(repo_url)
    if local.is_dir() and (local / ".git").exists():
        return str(local)
    if local.is_dir() and (local / "HEAD").exists():
        return str(local)

    name = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
    bare_path = Path(clone_dir) / f"{name}.git"

    if bare_path.exists():
        fetch_args = ["fetch", "--progress"]
        # Deepen the shallow boundary if needed. Cheap no-op when already
        # covered. We deliberately add a 1-day buffer so a `since="7 days
        # ago"` window doesn't fall off the edge of the shallow region.
        if shallow_since:
            fetch_args.append(f"--shallow-since={shallow_since}")
        if single_branch:
            fetch_args += ["origin", single_branch, "--prune"]
        else:
            fetch_args += ["--all", "--prune"]
        _run_git_streaming(
            fetch_args,
            cwd=str(bare_path),
            timeout=_SLOW_GIT_TIMEOUT,
            log_prefix="  git fetch:",
        )
        return str(bare_path)

    bare_path.parent.mkdir(parents=True, exist_ok=True)
    clone_args = ["clone", "--bare", "--progress"]
    if clone_filter:
        clone_args.append(f"--filter={clone_filter}")
    if shallow_since:
        clone_args.append(f"--shallow-since={shallow_since}")
    if single_branch:
        clone_args += ["--single-branch", "--branch", single_branch]
    clone_args += [repo_url, str(bare_path)]
    _run_git_streaming(
        clone_args,
        timeout=_SLOW_GIT_TIMEOUT,
        log_prefix="  git clone:",
    )
    return str(bare_path)


def is_blobless_clone(repo_path: str) -> bool:
    """Detect whether an existing bare clone was created with a blob filter.

    Lets the pipeline warn the user that diff walks will be slow and that
    deleting the cached clone will let the next run pull a full clone.
    """
    try:
        out = _run_git(
            ["config", "--get", "remote.origin.partialclonefilter"],
            cwd=repo_path,
        )
        return bool(out.strip())
    except RuntimeError:
        return False


def _parse_co_authors(raw: str) -> list[str]:
    """Parse the `|`-separated list of Co-authored-by trailer values."""
    if not raw:
        return []
    return [c.strip() for c in raw.split("|") if c.strip()]


def _parse_log_with_diffs(output: str) -> list[tuple[CommitInfo, str]]:
    """Split the output of `git log -p --format=_LOG_FORMAT ...` into commits + diffs."""
    if not output.strip():
        return []

    # The first segment before the first marker is empty (or trailing newline from git).
    segments = output.split(_COMMIT_MARKER)
    results: list[tuple[CommitInfo, str]] = []
    for seg in segments:
        if not seg.strip():
            continue
        # Header is up to the first newline; diff is everything after the blank line.
        header, _, body = seg.partition("\n")
        fields = header.split(_FIELD_SEP)
        if len(fields) < 6:
            continue
        commit = CommitInfo(
            hash=fields[0],
            date=fields[1],
            author=fields[2],
            author_email=fields[3],
            co_authors=_parse_co_authors(fields[4]),
            subject=fields[5],
        )
        # Strip a single leading blank line that git inserts between header and diff.
        diff = body.lstrip("\n")
        results.append((commit, diff))
    return results


def get_commits_with_diffs(
    repo_path: str,
    branch: str,
    start_commit: str | None,
    end_commit: str = "HEAD",
    max_commits: int = 500,
    since: str | None = None,
) -> list[tuple[CommitInfo, str]]:
    """Fetch commits AND their diffs in a single `git log -p` call.

    Returns commits in chronological order (oldest first). Streams the blobs
    from the blobless clone in one HTTP round-trip per branch rather than one
    per commit.
    """
    base_args = [
        "log",
        f"--format={_LOG_FORMAT}",
        "--no-merges",
        "--reverse",
        f"-n{max_commits}",
        "-p",
    ]
    if since:
        base_args.append(f"--since={since}")

    def _try(extra: list[str]) -> str:
        return _run_git(base_args + extra, cwd=repo_path)

    if start_commit:
        range_spec = f"{start_commit}..{end_commit}"
        try:
            output = _try([range_spec, "--", branch])
        except RuntimeError:
            output = _try([f"{start_commit}..refs/heads/{branch}"])
    else:
        try:
            output = _try([branch])
        except RuntimeError:
            output = _try([f"refs/heads/{branch}"])

    return _parse_log_with_diffs(output)


def get_diff_stat(repo_path: str, commit_hash: str) -> str:
    """Get the diffstat (file list + line counts) for a commit."""
    return _run_git(["show", "--format=", "--stat", commit_hash], cwd=repo_path)

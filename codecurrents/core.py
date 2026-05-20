"""Core orchestrator — the single high-level entrypoint."""

import asyncio
import re
import sys

from .config import CodeCurrentsConfig, NO_MAJOR_CHANGES, is_no_major_changes
from .state import load_state, save_state
from .git_ops import ensure_repo, get_commits_with_diffs, CommitInfo, normalize_repo_url, is_blobless_clone
from .filters import is_low_signal_commit, filter_diff
from .chunker import chunk_commits, format_chunk, make_token_counter
from .llm import map_pass, reduce_pass
from .summaries import save_summaries
from .chunk_cache import chunk_key, load_cached, save_cached
from . import projects as projects_mod


def _shallow_bound(since: str | None) -> str | None:
    """Pad the user's `since` with a 1-day buffer to use as the shallow boundary.

    Git's `--shallow-since` is exclusive on the date; if the user asks for
    "7 days ago" and the boundary lands on exactly that timestamp, commits
    right at the edge can fall outside the shallow region. We add a day so
    git always brings in at least one parent past the analysis window.
    """
    if not since:
        return None
    s = since.strip().lower()
    # "<N> days/weeks/months/years ago" → bump by 1 unit of granularity
    m = re.match(r"^(\d+)\s+(day|days|week|weeks|month|months|year|years)\s+ago$", s)
    if m:
        n = int(m.group(1))
        unit = m.group(2).rstrip("s")
        return f"{n + 1} {unit}s ago"
    # Bare date / unknown form: just pass it through. Git will interpret it.
    return since


def summarize_repo(config: CodeCurrentsConfig) -> str:
    """Fetch commits, filter, chunk, map-reduce summarize.

    Returns the final summary string, or "NO_MAJOR_CHANGES".
    """
    # Canonical URL used for state keying and filenames; git ops use config.repo_url
    # so SSH auth credentials are preserved for clone/fetch.
    repo_key = normalize_repo_url(config.repo_url)

    # 1. Determine how to select commits: --since, --start-commit, or state
    start = config.start_commit
    since = config.since

    if config.reprocess:
        print("Reprocess mode: ignoring saved state", file=sys.stderr)
    elif not start and not since:
        state = load_state(config.state_dir, repo_key)
        start = state.get("last_commit")

    if since:
        print(f"Fetching commits since: {since}", file=sys.stderr)
    elif start:
        print(f"Starting from commit: {start[:10]}", file=sys.stderr)
    else:
        print(f"First run — fetching last {config.max_commits} commits", file=sys.stderr)

    # 2. Clone or fetch the repo. If exactly one branch is configured, restrict
    # clone/fetch to that ref (compounds with the blobless filter).
    single_branch = config.branches[0] if len(config.branches) == 1 else None
    # When the user asked for a specific time window, use it as the shallow
    # boundary so the first clone is tiny. We add a 1-day buffer so commits
    # right at the edge of the window don't fall outside the shallow region.
    shallow_since = _shallow_bound(config.since)
    if shallow_since:
        print(
            f"Ensuring repo is available (shallow clone since {shallow_since})...",
            file=sys.stderr,
        )
    else:
        print(f"Ensuring repo is available...", file=sys.stderr)
    repo_path = ensure_repo(
        config.repo_url,
        config.clone_dir,
        single_branch=single_branch,
        clone_filter=config.clone_filter,
        shallow_since=shallow_since,
    )
    print(f"Repo at: {repo_path}", file=sys.stderr)
    if is_blobless_clone(repo_path):
        print(
            f"NOTE: this clone is blobless (--filter=blob:none). Diff walks lazy-fetch "
            f"every missing blob over HTTPS, which can take 30–60s per analysis. "
            f"For fast subsequent runs, delete {repo_path} and re-analyze — the next "
            f"run will do a full bare clone.",
            file=sys.stderr,
        )

    # 3. Collect commits + diffs across branches in one round-trip per branch.
    all_commits: dict[str, tuple[CommitInfo, str]] = {}
    per_branch_counts: dict[str, int] = {}

    for branch in config.branches:
        print(f"Fetching commits + diffs from branch: {branch}", file=sys.stderr)
        rows = get_commits_with_diffs(
            repo_path,
            branch,
            start,
            config.end_commit,
            config.max_commits,
            since=since,
        )
        per_branch_counts[branch] = len(rows)
        print(f"  Found {len(rows)} commits on {branch}", file=sys.stderr)
        for commit, raw_diff in rows:
            all_commits.setdefault(commit.hash, (commit, raw_diff))

    # Sort chronologically (ISO date strings sort correctly).
    sorted_rows = sorted(all_commits.values(), key=lambda cd: cd[0].date)
    sorted_commits = [c for c, _ in sorted_rows]
    print(f"Total unique commits: {len(sorted_rows)}", file=sys.stderr)

    # Fail loud if any branch hit max_commits — silently dropping the oldest
    # commits in a window is worse than refusing to run.
    for branch, n in per_branch_counts.items():
        if n >= config.max_commits:
            raise RuntimeError(
                f"Branch {branch!r} hit max_commits cap ({config.max_commits}). "
                f"Older commits would be silently dropped. Either raise --max-commits, "
                f"narrow the window with --since / --start-commit, or split the run."
            )

    if not sorted_rows:
        print("No new commits to process.", file=sys.stderr)
        return NO_MAJOR_CHANGES

    # 4. Filter diffs (per-file ignore patterns, binary skip, large-file truncation)
    print("Filtering commit diffs...", file=sys.stderr)
    count_tokens = make_token_counter(config.model)
    commits_with_diffs: list[tuple[CommitInfo, str]] = []
    skipped = 0
    total_tokens = 0

    for commit, raw_diff in sorted_rows:
        if is_low_signal_commit(commit.subject):
            skipped += 1
            continue

        filtered = filter_diff(raw_diff, config.ignore_patterns)
        if not filtered.strip():
            skipped += 1
            continue

        total_tokens += count_tokens(filtered)
        commits_with_diffs.append((commit, filtered))

    print(f"Kept {len(commits_with_diffs)} commits ({skipped} filtered out)", file=sys.stderr)
    print(f"Total diff tokens: ~{total_tokens:,}", file=sys.stderr)

    if not commits_with_diffs:
        print("All commits were filtered out.", file=sys.stderr)
        return NO_MAJOR_CHANGES

    # 5. Chunk
    chunks = chunk_commits(commits_with_diffs, config.tokens_per_chunk, counter=count_tokens)
    print(f"Split into {len(chunks)} chunks", file=sys.stderr)

    # 6. Format chunks (each chunk includes its commit timestamp range)
    formatted_chunks = [format_chunk(c) for c in chunks]

    # 7. Map pass — with per-chunk disk cache so retries are cheap.
    print("Running map pass...", file=sys.stderr)
    map_results = _run_map_with_cache(
        chunks=chunks,
        formatted_chunks=formatted_chunks,
        config=config,
        repo_key=repo_key,
    )

    successful = [(i, s) for i, s in enumerate(map_results) if s is not None]
    failed = [i for i, s in enumerate(map_results) if s is None]
    if failed:
        print(f"  {len(failed)}/{len(map_results)} chunks failed: {[i + 1 for i in failed]}", file=sys.stderr)
    print(f"Got {len(successful)} chunk summaries", file=sys.stderr)

    # Strict NO_MAJOR_CHANGES detection on per-chunk results
    substantive = [s for _, s in successful if not is_no_major_changes(s)]
    if not substantive:
        print("All chunks reported NO_MAJOR_CHANGES.", file=sys.stderr)
        if failed:
            print(
                f"WARNING: {len(failed)} chunk(s) failed — NOT advancing state.",
                file=sys.stderr,
            )
        else:
            last_hash = sorted_commits[-1].hash
            save_state(config.state_dir, repo_key, last_hash)
        if config.project_slug:
            projects_mod.save_run(
                config.project_slug,
                run_id=config.run_id or projects_mod.new_run_id(),
                repo_url=repo_key,
                model=config.model,
                branches=config.branches,
                since=config.since,
                commits=sorted_commits,
                chunks=chunks,
                map_summaries=map_results,
                reduce_summary=NO_MAJOR_CHANGES,
            )
        return NO_MAJOR_CHANGES

    # 8. Reduce pass
    print("Running reduce pass...", file=sys.stderr)
    final_summary = asyncio.run(reduce_pass(substantive, config.reduce_prompt, config.model))

    # 9. Save summaries to disk (positional — failures appear as placeholders)
    summary_path = save_summaries(
        output_dir=config.summaries_dir,
        repo_url=repo_key,
        model=config.model,
        commits=sorted_commits,
        chunks=chunks,
        map_summaries=map_results,
        reduce_summary=final_summary,
    )
    print(f"Summaries saved to: {summary_path}", file=sys.stderr)

    # 9b. Also write a structured run folder for the GUI, if this run is
    # associated with a project.
    if config.project_slug:
        run_dir = projects_mod.save_run(
            config.project_slug,
            run_id=config.run_id or projects_mod.new_run_id(),
            repo_url=repo_key,
            model=config.model,
            branches=config.branches,
            since=config.since,
            commits=sorted_commits,
            chunks=chunks,
            map_summaries=map_results,
            reduce_summary=final_summary,
        )
        print(f"Structured run saved to: {run_dir}", file=sys.stderr)

    # 10. Save state — but only if every chunk succeeded. Advancing past a
    # failed chunk on an incremental run would silently drop those commits
    # forever; the per-chunk disk cache only helps if we re-fetch them.
    if failed:
        print(
            f"WARNING: {len(failed)} chunk(s) failed — NOT advancing state. "
            f"Re-run to retry (cached successful chunks will be reused).",
            file=sys.stderr,
        )
    else:
        last_hash = sorted_commits[-1].hash
        save_state(config.state_dir, repo_key, last_hash)
        print(f"State saved. Last commit: {last_hash[:10]}", file=sys.stderr)

    if is_no_major_changes(final_summary):
        return NO_MAJOR_CHANGES
    return final_summary


def _run_map_with_cache(
    chunks: list[list[tuple[CommitInfo, str]]],
    formatted_chunks: list[str],
    config: CodeCurrentsConfig,
    repo_key: str,
) -> list[str | None]:
    """Map pass with on-disk per-chunk caching.

    Cache lives under <state_dir>/<repo>/chunks/<key>.json. The key folds in
    the chunk text + model + map prompt, so any of those changing invalidates.
    """
    cache_dir = config.state_dir
    keys = [chunk_key(text, config.model, config.map_prompt) for text in formatted_chunks]

    results: list[str | None] = [None] * len(formatted_chunks)
    pending_idx: list[int] = []
    pending_text: list[str] = []

    for i, (text, key) in enumerate(zip(formatted_chunks, keys)):
        cached = load_cached(cache_dir, repo_key, key)
        if cached is not None:
            print(f"  Chunk {i + 1}: cache hit", file=sys.stderr)
            results[i] = cached
        else:
            pending_idx.append(i)
            pending_text.append(text)

    if not pending_text:
        return results

    def _persist(local_idx: int, summary: str) -> None:
        global_idx = pending_idx[local_idx]
        save_cached(
            cache_dir,
            repo_key,
            keys[global_idx],
            summary,
            commits=[c for c, _ in chunks[global_idx]],
        )

    fresh = asyncio.run(
        map_pass(
            pending_text,
            config.map_prompt,
            config.model,
            max_concurrent=config.max_concurrent_requests,
            on_success=_persist,
        )
    )

    for local_idx, summary in enumerate(fresh):
        results[pending_idx[local_idx]] = summary

    return results

"""Core orchestrator — the single high-level entrypoint."""

import sys

from .config import CodeCurrentsConfig
from .state import load_state, save_state
from .git_ops import ensure_repo, get_commits, get_diff, CommitInfo
from .filters import is_low_signal_commit, filter_diff
from .chunker import chunk_commits, format_chunk, count_tokens
from .llm import map_pass, reduce_pass
from .summaries import save_summaries


def summarize_repo(config: CodeCurrentsConfig) -> str:
    """
    Main entrypoint: fetch commits, filter, chunk, map-reduce summarize.

    Returns the final summary string, or "NO_MAJOR_CHANGES".
    """
    # 1. Determine how to select commits: --since, --start-commit, or state
    start = config.start_commit
    since = config.since

    if config.reprocess:
        # Ignore saved state — reprocess all commits in the given range
        print("Reprocess mode: ignoring saved state", file=sys.stderr)
    elif not start and not since:
        state = load_state(config.state_dir, config.repo_url)
        start = state.get("last_commit")

    if since:
        print(f"Fetching commits since: {since}", file=sys.stderr)
    elif start:
        print(f"Starting from commit: {start[:10]}", file=sys.stderr)
    else:
        print(f"First run — fetching last {config.max_commits} commits", file=sys.stderr)

    # 2. Clone or fetch the repo
    print(f"Ensuring repo is available...", file=sys.stderr)
    repo_path = ensure_repo(config.repo_url, config.clone_dir)
    print(f"Repo at: {repo_path}", file=sys.stderr)

    # 3. Collect commits from all branches
    all_commits: dict[str, CommitInfo] = {}  # hash -> CommitInfo, deduplicates

    for branch in config.branches:
        print(f"Fetching commits from branch: {branch}", file=sys.stderr)
        commits = get_commits(repo_path, branch, start, config.end_commit, config.max_commits, since=since)
        print(f"  Found {len(commits)} commits on {branch}", file=sys.stderr)
        for c in commits:
            all_commits[c.hash] = c

    # Sort chronologically (by date string — ISO format sorts correctly)
    sorted_commits = sorted(all_commits.values(), key=lambda c: c.date)
    print(f"Total unique commits: {len(sorted_commits)}", file=sys.stderr)

    if not sorted_commits:
        print("No new commits to process.", file=sys.stderr)
        return "NO_MAJOR_CHANGES"

    # 4. Filter and get diffs
    print("Filtering commits and fetching diffs...", file=sys.stderr)
    commits_with_diffs: list[tuple[CommitInfo, str]] = []
    skipped = 0
    total_tokens = 0

    for commit in sorted_commits:
        # Skip low-signal commits
        if is_low_signal_commit(commit.subject):
            skipped += 1
            continue

        # Get and filter the diff
        try:
            raw_diff = get_diff(repo_path, commit.hash)
        except RuntimeError:
            skipped += 1
            continue

        filtered = filter_diff(raw_diff, config.ignore_patterns)

        # Skip if nothing left after filtering
        if not filtered.strip():
            skipped += 1
            continue

        tokens = count_tokens(filtered)
        total_tokens += tokens
        commits_with_diffs.append((commit, filtered))

    print(f"Kept {len(commits_with_diffs)} commits ({skipped} filtered out)", file=sys.stderr)
    print(f"Total diff tokens: ~{total_tokens:,}", file=sys.stderr)

    if not commits_with_diffs:
        print("All commits were filtered out.", file=sys.stderr)
        return "NO_MAJOR_CHANGES"

    # 5. Chunk commits
    chunks = chunk_commits(commits_with_diffs, config.tokens_per_chunk)
    print(f"Split into {len(chunks)} chunks", file=sys.stderr)

    # 6. Format chunks for LLM
    formatted_chunks = [format_chunk(c) for c in chunks]

    # 7. Map pass
    print("Running map pass...", file=sys.stderr)
    summaries = map_pass(formatted_chunks, config.map_prompt, config.model)
    print(f"Got {len(summaries)} chunk summaries", file=sys.stderr)

    # Check if all summaries are NO_MAJOR_CHANGES
    substantive = [s for s in summaries if "NO_MAJOR_CHANGES" not in s]
    if not substantive:
        print("All chunks reported NO_MAJOR_CHANGES.", file=sys.stderr)
        # Still save state so we don't reprocess
        last_hash = sorted_commits[-1].hash
        save_state(config.state_dir, config.repo_url, last_hash)
        return "NO_MAJOR_CHANGES"

    # 8. Reduce pass
    print("Running reduce pass...", file=sys.stderr)
    final_summary = reduce_pass(summaries, config.reduce_prompt, config.model)

    # 9. Save summaries to disk
    summary_path = save_summaries(
        output_dir=config.summaries_dir,
        repo_url=config.repo_url,
        model=config.model,
        commits=sorted_commits,
        chunks=chunks,
        map_summaries=summaries,
        reduce_summary=final_summary,
    )
    print(f"Summaries saved to: {summary_path}", file=sys.stderr)

    # 10. Save state
    last_hash = sorted_commits[-1].hash
    save_state(config.state_dir, config.repo_url, last_hash)
    print(f"State saved. Last commit: {last_hash[:10]}", file=sys.stderr)

    return final_summary

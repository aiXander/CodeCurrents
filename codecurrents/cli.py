"""CLI entrypoint for CodeCurrents."""

import argparse
import json
import os
import sys
from pathlib import Path

from .config import (
    CodeCurrentsConfig,
    DEFAULT_IGNORE_PATTERNS,
    DEFAULT_MAP_PROMPT,
    DEFAULT_REDUCE_PROMPT,
    DEFAULT_TOKENS_PER_CHUNK,
    DEFAULT_MAX_CONCURRENT_REQUESTS,
)
from .core import summarize_repo


def _load_env_file() -> None:
    """Load environment variables from .env in the current working directory."""
    for env_path in [Path(".env"), Path.home() / ".eve", Path.home() / ".env"]:
        if not env_path.exists():
            continue
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            # Strip surrounding quotes
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if key not in os.environ:
                os.environ[key] = value


def main() -> None:
    _load_env_file()

    parser = argparse.ArgumentParser(
        prog="codecurrents",
        description="Summarize code changes from a Git repo using LLM map-reduce.",
    )
    parser.add_argument("--repo", required=True, help="Git repo URL or local path")
    parser.add_argument("--branches", nargs="+", default=["main"], help="Branches to track (default: main). When exactly one branch is given, the clone/fetch is restricted to that ref.")
    parser.add_argument("--since", default=None, help="Time-based filter, e.g. '9 days ago', '2026-03-01' (passed to git --since)")
    parser.add_argument("--start-commit", default=None, help="Start commit hash (default: read from state)")
    parser.add_argument("--end-commit", default="HEAD", help="End commit hash (default: HEAD)")
    parser.add_argument("--model", default="anthropic/claude-haiku-4-5", help="LLM model (provider/model for OpenRouter, or claude-*, gpt-*, gemini-* for direct backends)")
    parser.add_argument("--map-prompt", default=None, help="Custom map prompt (or path to .txt file)")
    parser.add_argument("--reduce-prompt", default=None, help="Custom reduce prompt (or path to .txt file)")
    parser.add_argument("--config", default=None, help="Path to JSON config file")
    parser.add_argument("--ignore", nargs="+", default=None, help="Additional file patterns to ignore")
    parser.add_argument("--tokens-per-chunk", type=int, default=DEFAULT_TOKENS_PER_CHUNK, help="Target tokens per chunk")
    parser.add_argument("--max-commits", type=int, default=500, help="Max commits to process")
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=DEFAULT_MAX_CONCURRENT_REQUESTS,
        help=(
            "Max concurrent LLM requests during the map fan-out. "
            "Higher = faster but more likely to hit provider rate limits. "
            "Default: %(default)s. Also configurable as max_concurrent_requests in config.json."
        ),
    )
    parser.add_argument("--state-dir", default="./state", help="Directory for state files")
    parser.add_argument("--clone-dir", default="/tmp/codecurrents_repos", help="Directory for cloned repos")
    parser.add_argument("--summaries-dir", default="./summaries", help="Directory for saved LLM summary logs")
    parser.add_argument("--reprocess", action="store_true", default=False, help="Ignore saved state and reprocess all commits in the given range")

    args = parser.parse_args()

    # Load config file — explicit path or auto-discover config.json in cwd
    file_config = {}
    config_path = Path(args.config) if args.config else Path("config.json")
    if args.config and not config_path.exists():
        print(f"Error: config file not found: {args.config}", file=sys.stderr)
        sys.exit(1)
    if config_path.exists():
        print(f"Loading config: {config_path}", file=sys.stderr)
        file_config = json.loads(config_path.read_text())

    # Resolve prompts (can be inline text or path to a .txt file)
    map_prompt = _resolve_prompt(args.map_prompt or file_config.get("map_prompt"), DEFAULT_MAP_PROMPT)
    reduce_prompt = _resolve_prompt(args.reduce_prompt or file_config.get("reduce_prompt"), DEFAULT_REDUCE_PROMPT)

    # Merge ignore patterns
    ignore = list(DEFAULT_IGNORE_PATTERNS)
    if file_config.get("ignore_patterns"):
        ignore.extend(file_config["ignore_patterns"])
    if args.ignore:
        ignore.extend(args.ignore)

    config = CodeCurrentsConfig(
        repo_url=args.repo,
        branches=file_config.get("branches", args.branches),
        since=_normalize_since(args.since or file_config.get("since")),
        start_commit=args.start_commit or file_config.get("start_commit"),
        end_commit=args.end_commit,
        model=file_config.get("model", args.model),
        map_prompt=map_prompt,
        reduce_prompt=reduce_prompt,
        ignore_patterns=ignore,
        tokens_per_chunk=file_config.get("tokens_per_chunk", args.tokens_per_chunk),
        max_commits=file_config.get("max_commits", args.max_commits),
        max_concurrent_requests=file_config.get("max_concurrent_requests", args.max_concurrent),
        state_dir=file_config.get("state_dir", args.state_dir),
        clone_dir=file_config.get("clone_dir", args.clone_dir),
        summaries_dir=file_config.get("summaries_dir", args.summaries_dir),
        reprocess=args.reprocess or file_config.get("reprocess", False),
    )

    # Run
    result = summarize_repo(config)

    # Output
    if result == "NO_MAJOR_CHANGES":
        print("\n--- No major changes detected. ---\n")
    else:
        print("\n" + result)


def _normalize_since(value: str | None) -> str | None:
    """Strip and collapse internal whitespace so '7  days ago' → '7 days ago'."""
    if value is None:
        return None
    return " ".join(value.split())


def _resolve_prompt(value: str | None, default: str) -> str:
    """If value is a file path, read it; otherwise return it as-is."""
    if value is None:
        return default
    # Only treat short values as potential file paths to avoid OS errors
    if len(value) <= 4096:
        path = Path(value)
        try:
            if path.exists() and path.is_file():
                return path.read_text().strip()
        except OSError:
            pass
    return value


if __name__ == "__main__":
    main()

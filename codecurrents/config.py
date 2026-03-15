"""Configuration and defaults for CodeCurrents."""

from dataclasses import dataclass, field


DEFAULT_IGNORE_PATTERNS: list[str] = [
    # Lock files
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Pipfile.lock",
    "poetry.lock",
    "go.sum",
    "Cargo.lock",
    "composer.lock",
    "Gemfile.lock",
    # Binary / asset files
    "*.svg",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.ico",
    "*.woff",
    "*.woff2",
    "*.ttf",
    "*.eot",
    # Minified / bundled
    "*.min.js",
    "*.min.css",
    "*.bundle.js",
    "*.chunk.js",
    "*.map",
    # Auto-generated
    "*/migrations/*",
    "*.pb.go",
    "*_generated.*",
    "*.auto.*",
    "**/generated/**",
    # Snapshots / fixtures
    "**/__snapshots__/**",
]

DEFAULT_MAP_PROMPT = """\
You are a senior software architect analyzing code changes in a software repository.

Given the following set of git commits and their diffs, produce a concise summary focusing on:
- Significant architectural decisions or patterns being introduced
- New features or capabilities being added
- Major refactors or structural changes
- Shifts in technology choices or dependencies

Do NOT focus on:
- Individual bug fixes or minor tweaks
- Formatting or style changes
- Test-only changes (unless they reveal architectural intent)

If the changes are purely maintenance, bug fixes, or minor tweaks with no architectural significance, \
output exactly: NO_MAJOR_CHANGES

Keep your summary to 300-500 words. Be specific about what changed and why it matters architecturally.\
"""

DEFAULT_REDUCE_PROMPT = """\
You are a senior software architect producing a high-level summary of recent development activity.

Below are summaries of different batches of code changes from the same repository. \
Synthesize them into a single, coherent narrative that captures:

1. The overall direction the codebase is heading
2. Major new features or capabilities that emerged
3. Significant architectural decisions or design patterns being adopted
4. Any notable technology shifts

Write in a clear, newsletter-style tone suitable for developers who follow this project. \
Avoid repeating the same point from multiple summaries — merge and deduplicate.

If ALL summaries indicate NO_MAJOR_CHANGES, output exactly: NO_MAJOR_CHANGES

Otherwise, ignore any NO_MAJOR_CHANGES summaries and focus on the substantive ones.

Target length: 400-800 words.\
"""

# Commit message patterns that indicate low-signal commits
LOW_SIGNAL_PATTERNS: list[str] = [
    r"^Merge (branch|pull request|remote)",
    r"^Bump \w",
    r"^Update dependency",
    r"^\[dependabot\]",
    r"^\[renovate\]",
    r"^chore\(deps\)",
    r"^Auto-merge",
    r"^Automatic merge",
]

# Max lines in a single file diff before we truncate it
MAX_DIFF_LINES_PER_FILE = 2000

# Safety limit on total commits to process
MAX_COMMITS_DEFAULT = 500


@dataclass
class CodeCurrentsConfig:
    """Full configuration for a CodeCurrents run."""

    repo_url: str
    branches: list[str] = field(default_factory=lambda: ["main"])
    start_commit: str | None = None
    end_commit: str = "HEAD"
    model: str = "claude-haiku-4-5-20251001"
    map_prompt: str = DEFAULT_MAP_PROMPT
    reduce_prompt: str = DEFAULT_REDUCE_PROMPT
    ignore_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE_PATTERNS))
    tokens_per_chunk: int = 40_000
    max_commits: int = MAX_COMMITS_DEFAULT
    state_dir: str = "./state"
    summaries_dir: str = "./summaries"
    clone_dir: str = "/tmp/codecurrents_repos"
    since: str | None = None  # e.g. "9 days ago", "2026-03-01", passed to git log --since
    reprocess: bool = False  # ignore saved state and reprocess all commits in range

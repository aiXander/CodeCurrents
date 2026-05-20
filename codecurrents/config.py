"""Configuration and defaults for CodeCurrents."""

import re
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

NO_MAJOR_CHANGES = "NO_MAJOR_CHANGES"


def is_no_major_changes(text: str) -> bool:
    """Strict check for the NO_MAJOR_CHANGES sentinel.

    Matches only when the model emits the sentinel on its own (optionally with
    trailing punctuation/whitespace) — not when it's mentioned mid-prose.
    """
    if not text:
        return False
    stripped = text.strip()
    return bool(re.fullmatch(r"NO_MAJOR_CHANGES[.\s]*", stripped))


DEFAULT_MAP_PROMPT = """\
You are a senior software architect analyzing a batch of git commits.

Output **exactly** the following Markdown skeleton — keep section headers verbatim, \
omit a section's bullets if it has nothing substantive, but do not rename or reorder sections.

### Summary
One or two sentences naming the dominant theme of this batch.

### New features & capabilities
- Bullet per distinct feature/capability. Name the module/file paths.

### Refactors & architectural shifts
- Bullet per refactor, abstraction, or design-pattern change. Be specific about \
what moved, what was renamed, what was extracted.

### Dependencies & infrastructure
- Added/removed/upgraded deps, build/CI changes, deployment or runtime shifts.

### Risks & follow-ups
- Anything fragile, half-done, or worth a reviewer's attention.

### Contributors
- Names of authors / co-authors who drove notable changes in this batch (skip if unclear).

Rules:
- Ground every bullet in a specific commit, file path, or module name. No generic filler.
- Ignore typo fixes, formatting, style-only edits, and test-only churn unless they reveal \
architectural intent.
- If the batch is purely maintenance, bug fixes, or trivial tweaks, output exactly: \
NO_MAJOR_CHANGES (and nothing else).
- Target 250–500 words total across all sections.\
"""

DEFAULT_REDUCE_PROMPT = """\
You are a senior software architect writing a development digest for a codebase.

Below are structured per-batch summaries of recent code changes. Synthesize them \
into a single newsletter-style narrative:

1. Lead with the overall direction the codebase is heading.
2. Group related bullets across batches — merge and deduplicate aggressively.
3. Surface notable architectural decisions and design-pattern shifts.
4. Call out infrastructure / dependency / tooling changes worth knowing.
5. Mention contributors when a thread is clearly driven by specific people.

Style:
- Clear, plain prose. Newsletter voice, not bullet-soup.
- Cite specific module / file names where they sharpen the point.
- Use the per-batch timestamp ranges (visible in the input) to give the narrative \
a sense of time.

If ALL batches indicate NO_MAJOR_CHANGES, output exactly: NO_MAJOR_CHANGES

Otherwise, silently ignore any NO_MAJOR_CHANGES batches.

Target length: 400–800 words.\
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

# Target tokens per chunk for the map pass
DEFAULT_TOKENS_PER_CHUNK = 50_000

# Default cap on concurrent LLM requests during the map fan-out.
# Tune via --max-concurrent or "max_concurrent_requests" in config.json.
# Too high → provider rate-limit 429s + retry storms; too low → slow runs.
DEFAULT_MAX_CONCURRENT_REQUESTS = 8


@dataclass
class CodeCurrentsConfig:
    """Full configuration for a CodeCurrents run."""

    repo_url: str
    branches: list[str] = field(default_factory=lambda: ["main"])
    start_commit: str | None = None
    end_commit: str = "HEAD"
    model: str = "anthropic/claude-haiku-4-5"
    map_prompt: str = DEFAULT_MAP_PROMPT
    reduce_prompt: str = DEFAULT_REDUCE_PROMPT
    ignore_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE_PATTERNS))
    tokens_per_chunk: int = DEFAULT_TOKENS_PER_CHUNK
    max_commits: int = MAX_COMMITS_DEFAULT
    # Maximum number of map-pass LLM calls in flight at once. Higher values
    # finish faster but risk hitting provider rate limits; lower values are
    # safer on shared/free tiers. The map pass uses asyncio.Semaphore(this).
    max_concurrent_requests: int = DEFAULT_MAX_CONCURRENT_REQUESTS
    state_dir: str = "./state"
    summaries_dir: str = "./summaries"
    clone_dir: str = "/tmp/codecurrents_repos"
    # Partial-clone filter (e.g. "blob:none"). None = full bare clone, which
    # is the right default for repeated analysis: bigger disk, but git log -p
    # is purely local. Set to "blob:none" to save disk if you only ever run
    # once.
    clone_filter: str | None = None
    since: str | None = None  # e.g. "9 days ago", "2026-03-01", passed to git log --since
    reprocess: bool = False  # ignore saved state and reprocess all commits in range
    # When set, summarize_repo also writes a structured run folder under
    # projects/<project_slug>/runs/<run_id>/ in addition to the legacy log.
    # The frontend reads from there. CLI users who don't pass --project leave it None.
    project_slug: str | None = None
    run_id: str | None = None

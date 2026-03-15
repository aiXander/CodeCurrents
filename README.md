# CodeCurrents

Turn weeks of git history into a concise, narrative summary. CodeCurrents reads commit diffs from any Git repository and uses an LLM map-reduce pipeline to distill them into a high-level report of what changed and why it matters architecturally.

## How It Works

```
  git log ──▶ filter ──▶ chunk ──▶ map (LLM) ──▶ reduce (LLM) ──▶ summary
              │                     │ chunk 1 │
              ├ skip merges         │ chunk 2 │──▶ final narrative
              ├ skip dep bumps      │ chunk 3 │
              └ skip lockfiles      │  ...    │
```

1. **Clone / fetch** the repo as a bare git repo (or use a local path directly)
2. **Extract** commits and diffs for the requested time range or commit range
3. **Filter** out noise — merge commits, dependency bumps, renovate/dependabot, lockfiles, binary assets, minified bundles, generated code
4. **Chunk** the remaining diffs into token-bounded groups (~40–50k tokens each), truncating oversized commits via binary search
5. **Map** — summarize each chunk in parallel (3 concurrent workers) through the LLM
6. **Reduce** — synthesize all chunk summaries into a single coherent narrative
7. **Persist** — save the full summary log to `./summaries/` and record the last processed commit in `./state/` so the next run picks up where you left off

## Installation

Requires **Python 3.10+** and **git** on your PATH.

```bash
git clone https://github.com/XanderSteenworx/CodeCurrents.git
cd CodeCurrents

# Core install (Anthropic/Claude backend)
pip install -e .

# All LLM backends (adds OpenAI + Gemini)
pip install -e ".[all]"

# Or install individual backends
pip install -e ".[openai]"
pip install -e ".[gemini]"
```

## API Keys

Set the key for whichever LLM provider you want to use:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."   # Anthropic (default)
export OPENAI_API_KEY="sk-..."          # OpenAI
export GOOGLE_API_KEY="AI..."           # Google Gemini
```

You can also place these in `~/.env` or `~/.eve` — CodeCurrents loads them automatically.

## Quick Start

```bash
# Summarize the last 7 days of a GitHub repo
codecurrents --repo https://github.com/owner/repo --since "7 days ago"

# Summarize a local repo since a specific date
codecurrents --repo /path/to/repo --since "2026-01-01"

# Use a config file for recurring runs
codecurrents --repo https://github.com/owner/repo --config config.json
```

The final summary prints to **stdout**. Progress and diagnostics go to **stderr**, so you can pipe the summary cleanly:

```bash
codecurrents --repo https://github.com/owner/repo --since "30 days ago" > summary.md
```

## CLI Reference

```
codecurrents --repo <url-or-path> [options]
```

| Flag | Default | Description |
|---|---|---|
| `--repo` | *(required)* | Git repo URL or local path |
| `--since` | `null` | Time filter passed to `git log --since` (e.g. `"7 days ago"`, `"2026-03-01"`) |
| `--branches` | `main` | Space-separated branches to analyze |
| `--start-commit` | from state | Start from this commit hash |
| `--end-commit` | `HEAD` | End at this commit hash |
| `--model` | `claude-haiku-4-5-20251001` | LLM model to use (see [Supported Models](#supported-models)) |
| `--map-prompt` | built-in | Custom prompt for the map pass — inline text or path to a `.txt` file |
| `--reduce-prompt` | built-in | Custom prompt for the reduce pass — inline text or path to a `.txt` file |
| `--config` | `null` | Path to a JSON config file |
| `--ignore` | `[]` | Additional file glob patterns to ignore (space-separated) |
| `--tokens-per-chunk` | `50000` | Target token count per chunk |
| `--max-commits` | `500` | Maximum commits to process per run |
| `--state-dir` | `./state` | Directory for state persistence files |
| `--clone-dir` | `/tmp/codecurrents_repos` | Directory for bare-cloned repos |
| `--summaries-dir` | `./summaries` | Directory for saved summary logs |
| `--reprocess` | `false` | Ignore saved state and reprocess all commits in the given range |

## Supported Models

The LLM backend is auto-detected from the model name prefix:

| Prefix | Provider | Example models |
|---|---|---|
| `claude-*` | Anthropic | `claude-haiku-4-5-20251001`, `claude-sonnet-4-6` |
| `gpt-*`, `o1-*`, `o3-*`, `o4-*` | OpenAI | `gpt-4o`, `o3-mini` |
| `gemini-*` | Google Gemini | `gemini-2.5-flash`, `gemini-2.5-pro` |

If the prefix is ambiguous, CodeCurrents falls back by checking which API key is set (Anthropic → OpenAI → Gemini).

```bash
# OpenAI
codecurrents --repo https://github.com/owner/repo --since "7 days ago" --model gpt-4o

# Gemini
codecurrents --repo https://github.com/owner/repo --since "7 days ago" --model gemini-2.5-flash

# Anthropic (default)
codecurrents --repo https://github.com/owner/repo --since "7 days ago" --model claude-sonnet-4-6
```

## Configuration File

For recurring runs, use a JSON config file instead of repeating CLI flags. CLI flags always take precedence over config file values.

```json
{
  "model": "gemini-2.5-flash",
  "branches": ["main", "develop"],
  "since": "14 days ago",
  "tokens_per_chunk": 50000,
  "max_commits": 500,
  "ignore_patterns": ["docs/**", "*.md"],
  "map_prompt": "Summarize these changes in 3 bullet points.",
  "reduce_prompt": "Synthesize these batch summaries into a coherent narrative...",
  "state_dir": "./state",
  "clone_dir": "/tmp/codecurrents_repos",
  "summaries_dir": "./summaries"
}

```

See [`config.example.json`](config.example.json) for a full example.

Prompts can also point to a `.txt` file:

```json
{
  "map_prompt": "prompts/map.txt",
  "reduce_prompt": "prompts/reduce.txt"
}
```

## Incremental Runs (State Tracking)

CodeCurrents remembers the last commit it processed for each repository, stored as JSON files in the `--state-dir` directory (default: `./state/`).

On subsequent runs **without** `--since` or `--start-commit`, it automatically picks up where it left off:

```bash
# First run — processes last 500 commits
codecurrents --repo https://github.com/owner/repo

# Second run — only processes new commits since last run
codecurrents --repo https://github.com/owner/repo

# Force reprocessing (ignores saved state)
codecurrents --repo https://github.com/owner/repo --since "30 days ago" --reprocess
```

## What Gets Filtered Out

CodeCurrents aggressively filters noise so the LLM focuses on meaningful changes:

**Commits skipped by message pattern:**
- Merge commits (`Merge branch ...`, `Merge pull request ...`)
- Dependency bumps (`Bump ...`, `Update dependency ...`)
- Renovate / Dependabot (`[dependabot]`, `[renovate]`, `chore(deps)`)
- Auto-merges

**Files removed from diffs:**
- Lock files — `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `poetry.lock`, `go.sum`, `Cargo.lock`, `composer.lock`, `Gemfile.lock`
- Binary assets — images (`.png`, `.jpg`, `.gif`, `.svg`, `.ico`), fonts (`.woff`, `.woff2`, `.ttf`, `.eot`)
- Minified / bundled — `.min.js`, `.min.css`, `.bundle.js`, `.chunk.js`, `.map`
- Generated code — `*_generated.*`, `*.auto.*`, `*.pb.go`, `**/generated/**`
- Migrations — `*/migrations/*`
- Snapshots — `**/__snapshots__/**`

Add your own patterns with `--ignore` or `ignore_patterns` in the config file.

Individual file diffs exceeding 2,000 lines are truncated with a notice.

## Output

### Terminal

The final narrative summary prints to **stdout**. If all changes were low-signal, you'll see:

```
--- No major changes detected. ---
```

### Summary Logs

Each run appends a detailed log to `./summaries/<org_repo>.md` containing:

- Run metadata (model, commit range, date range, chunk count)
- Individual map summaries per chunk
- The final reduce summary

This creates a running history of your project's evolution that you can browse or search.

## Examples

### Analyze multiple branches

```bash
codecurrents --repo https://github.com/owner/repo --since "14 days ago" --branches main develop feature/v2
```

### Custom prompts for a security audit focus

```bash
codecurrents --repo https://github.com/owner/repo --since "30 days ago" \
  --map-prompt "Focus on security-relevant changes: auth, input validation, crypto, dependencies with CVEs." \
  --reduce-prompt "Produce a security-focused summary of all changes."
```

### Ignore documentation and test changes

```bash
codecurrents --repo https://github.com/owner/repo --since "7 days ago" \
  --ignore "docs/**" "**/*.test.*" "**/*.spec.*" "*.md"
```

### Process a specific commit range

```bash
codecurrents --repo https://github.com/owner/repo \
  --start-commit abc1234 --end-commit def5678
```

## Project Structure

```
codecurrents/
├── cli.py          # CLI argument parsing, config merging, entry point
├── config.py       # Defaults, dataclass config, built-in prompts
├── core.py         # Pipeline orchestrator (summarize_repo)
├── git_ops.py      # Git clone/fetch/log/diff via subprocess
├── filters.py      # Commit and diff filtering
├── chunker.py      # Token counting (tiktoken) and commit grouping
├── llm.py          # Multi-provider LLM calls with retry and parallelism
├── state.py        # Per-repo state persistence (JSON)
└── summaries.py    # Summary log persistence (Markdown)
```

## License

MIT

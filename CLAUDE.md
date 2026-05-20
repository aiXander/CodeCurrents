# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CodeCurrents is a Python CLI that distills weeks of git history into a high-level narrative summary. It clones (or reuses) a repo, walks commits in a time/range window, filters out noise (merges, dep bumps, lockfiles, generated code), then runs an LLM **map-reduce** over the diffs: each chunk of commits is summarized in parallel ("map"), and the chunk summaries are then synthesized into a single newsletter-style report ("reduce").

It is incremental by default — per-repo state in `./state/` records the last processed commit so subsequent runs only cover new history. Full per-run logs (map + reduce) are appended to `./summaries/<org>_<repo>.md`.

## Commands

```bash
# Install (core deps: anthropic + tiktoken)
pip install -e .

# With all backends (adds OpenAI + Gemini SDKs)
pip install -e ".[all]"
# Or individually: ".[openai]" / ".[gemini]"

# Run
codecurrents --repo <url-or-local-path> --since "7 days ago"
codecurrents --repo <url> --config config.json

# Force re-process the whole range, ignoring saved state
codecurrents --repo <url> --since "7 days ago" --reprocess
```

The final summary goes to **stdout**; all progress/diagnostics go to **stderr** — pipe stdout cleanly for redirection.

There are no tests, linters, or build steps configured. `pyproject.toml` declares the `codecurrents` entry point and `requires-python = ">=3.10"`.

## Architecture

Pipeline orchestrated by `core.py:summarize_repo()`. Each step is a small, single-purpose module:

| File | Responsibility |
|---|---|
| `cli.py` | Argument parsing, `.env` loading, config-file merging, prompt resolution |
| `config.py` | `CodeCurrentsConfig` dataclass, defaults, built-in prompts, low-signal regexes |
| `core.py` | The pipeline. Selects commits → filters → chunks → map → reduce → persists |
| `git_ops.py` | `ensure_repo` (clone/fetch bare), `get_commits_with_diffs` (one `git log -p` per branch, includes author + co-authors), URL normalization |
| `filters.py` | `is_low_signal_commit` (regex on subject), `filter_diff` (per-file fnmatch + binary skip + truncation) |
| `chunker.py` | Per-backend token counters (`make_token_counter(model)`), `chunk_commits`, signal-ranked per-file diff truncation, chunk header w/ timestamp range |
| `llm.py` | Backend dispatch (OpenRouter / Anthropic / OpenAI / Gemini), retry, async map/reduce with `asyncio.Semaphore` concurrency cap and positional results |
| `state.py` | Per-repo JSON state in `./state/<org>_<repo>.json` |
| `chunk_cache.py` | Per-chunk on-disk cache of map summaries under `./state/<repo>/chunks/<key>.json` |
| `summaries.py` | Appends each run's map + reduce summaries to `./summaries/<org>_<repo>.md` (failed chunks rendered as placeholders) |

### Pipeline (`core.summarize_repo`)

1. **Resolve start point.** `--reprocess` ignores state. Otherwise: `--start-commit` → `start_commit` from config.json → `last_commit` from `./state/<repo>.json` → fall back to last `max_commits` commits.
2. **`ensure_repo`.** If `--repo` is a local path with `.git/` or `HEAD/`, use it in place. Otherwise **blobless bare-clone** (`--bare --filter=blob:none`) into `--clone-dir` (default `/tmp/codecurrents_repos/<name>.git`). When `len(config.branches) == 1`, also adds `--single-branch --branch <name>` and limits subsequent fetches to that one ref; otherwise `git fetch --all --prune`. The blob filter is stored in repo config and honored automatically. Blobs are fetched lazily when `git show` runs, so cold per-commit diffs cost an HTTP round-trip; warm cache is free.
3. **Collect commits + diffs across branches.** `get_commits_with_diffs` runs **one** `git log -p` per branch (single HTTP round-trip on blobless clones) and parses commits + diffs from the interleaved output. Results are deduplicated by hash in a dict, then sorted by ISO date string (chronological). If any branch hits `max_commits`, the run **errors out** rather than silently dropping the oldest commits.
4. **Filter per commit.** Skip via `is_low_signal_commit(subject)`; per-file filter via `filter_diff` (drops ignored fnmatch patterns + binary files + truncates files >2000 lines). Drop commit if diff is empty after filtering.
5. **Chunk.** `chunk_commits` greedily packs commits up to `tokens_per_chunk`. Each chunk's formatted text gets a header announcing its commit count and timestamp range so the LLM can anchor the batch in time. Oversized single commits are token-truncated by ranking files by signal (low-signal paths like `tests/`, `generated/`, `node_modules/` get dropped first; larger files lose to smaller within a tier).
6. **Map pass with cache.** Each formatted chunk is hashed (chunk text + model + map prompt) and looked up in `./state/<repo>/chunks/<key>.json`. Cache hits skip the LLM call. Cache misses run through `map_pass`, which uses `asyncio.Semaphore(config.max_concurrent_requests)` to cap in-flight LLM calls. Successful summaries are written to the cache **as they complete** so a ctrl-C or downstream reduce failure doesn't lose paid-for work. Failed chunks return `None` (positional) and surface as `*[FAILED]*` placeholders in the saved log.
7. **Early exit on `NO_MAJOR_CHANGES`.** Detection is strict: a chunk only counts as NO_MAJOR_CHANGES if its summary equals the sentinel (modulo whitespace/punctuation). If every successful chunk is the sentinel, state is saved (so we don't reprocess), but summaries are **not** written and reduce is skipped.
8. **Reduce pass.** Concatenates chunk summaries with `## Batch N Summary` headers and feeds them through `reduce_prompt`.
9. **Persist.** `save_summaries` appends to `./summaries/<repo>.md` (run metadata + each map + the reduce). `save_state` writes the latest commit hash + ISO timestamp.

### Configuration precedence — **NOT a clean hierarchy**

`cli.py` builds the final `CodeCurrentsConfig` field-by-field with inconsistent precedence. Read the source before assuming. The pattern depends on the field:

| Field | Precedence pattern | Winner when both set |
|---|---|---|
| `branches`, `model`, `tokens_per_chunk`, `max_commits`, `max_concurrent_requests`, `state_dir`, `clone_dir`, `summaries_dir` | `file_config.get(key, args.x)` | **config file wins** |
| `since`, `start_commit`, `map_prompt`, `reduce_prompt` | `args.x or file_config.get(key)` | **CLI wins** |
| `ignore_patterns` | `DEFAULT + file_config + args` (all merged) | accumulates |
| `reprocess` | `args.x or file_config.get(...)` | OR'd — either turns it on |
| `end_commit` | CLI only | n/a |

If `--config` isn't passed, `cli.py` auto-loads `./config.json` if present and prints `Loading config: ...` to stderr. Pass an explicit `--config path` that doesn't exist and it errors out.

### Token budgets

`DEFAULT_TOKENS_PER_CHUNK = 50_000` in `config.py` (the dataclass default and the CLI default both use this constant; they're aligned). Token counting goes through `make_token_counter(model)`, which picks a per-family safety margin over `cl100k_base`: 1.0× for OpenAI (`gpt`/`o*`), 1.2× for Claude/Anthropic, 1.3× for Gemini/Google, 1.2× fallback. If `tiktoken` import fails, falls back to `len(text) // 3`. `max_tokens` on completions is hard-coded to 2048 in every backend; `temperature` is 0.3.

### Concurrency cap (`max_concurrent_requests`)

The map pass uses `asyncio.Semaphore(config.max_concurrent_requests)` (default `8`) to cap in-flight LLM calls. Tunable via `--max-concurrent` or `"max_concurrent_requests"` in config.json. Higher → faster runs but more likely to hit provider 429 rate limits (which trigger the 3-attempt `2/4/8s` backoff and waste tokens); lower → safer on free/shared tiers.

### LLM backend detection (`llm._detect_backend`)

Model string is matched in this order:
1. Contains `/` → **OpenRouter** (e.g. `anthropic/claude-haiku-4-5`, `google/gemini-3.1-flash-lite-preview`)
2. Starts with `claude` → **Anthropic** direct (`ANTHROPIC_API_KEY`)
3. Starts with `gpt`, `o1`, `o3`, `o4` → **OpenAI** direct
4. Starts with `gemini` → **Google Gemini** direct
5. Fallback: whichever API key is set, checked in order: OpenRouter → Anthropic → OpenAI → Gemini

**Default model is `anthropic/claude-haiku-4-5`** (OpenRouter route) — see `CodeCurrentsConfig.model` and the CLI `--model` default. The README's "supported models" table is direct-API-centric and slightly out of sync with that default.

Anthropic calls send the system prompt with `cache_control: {type: "ephemeral"}` to enable prompt caching across the map fan-out. SDKs are lazily imported inside each `_call_*` so installing only one extra works.

### Git ops gotchas (`git_ops.py`)

- `ensure_repo` detects both worktree (`.git/`) and bare (`HEAD/`) layouts for local paths.
- `get_commits` first tries `start..end -- branch`; on failure it retries with `refs/heads/branch` (handles bare-repo branch resolution differences).
- `_run_git` uses a **120s subprocess timeout** — long `git fetch` or `git show` on huge commits can blow it.
- `normalize_repo_url` strips trailing `/` and `.git`, resolves local paths to absolute, and rewrites `git@host:org/repo` → `https://host/org/repo` so SSH and HTTPS URLs share the same state file.
- The state filename uses `state.repo_name_from_url` (regex `([^/:]+)/([^/:]+)$` → `org_repo`); summaries use a different helper (`summaries._repo_filename`) with similar but not identical logic. Both produce `org_repo` for normal URLs.

### `NO_MAJOR_CHANGES` semantics

- A chunk-level all-NO_MAJOR_CHANGES result: state saved, summaries **not** written, no reduce run, returns the sentinel.
- A reduce-level NO_MAJOR_CHANGES: summaries **are** written, state saved, sentinel returned.
- CLI prints `--- No major changes detected. ---` (with surrounding blank lines) on the sentinel.

### Environment

`.env`-style files are auto-loaded (first match used, existing env vars not overridden):
1. `./.env` in the CWD
2. `~/.eve`
3. `~/.env`

Recognized API keys: `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`.

### Custom prompts

`--map-prompt` / `--reduce-prompt` (or the corresponding config keys) accept either inline text or a path to a `.txt` file. `_resolve_prompt` only treats values ≤4096 chars as candidate paths and silently falls back to inline if the path doesn't exist or read fails.

### Filter defaults

- `LOW_SIGNAL_PATTERNS` (regex on subject, case-insensitive): merges, `Bump …`, `Update dependency`, `[dependabot]`, `[renovate]`, `chore(deps)`, auto-merges.
- `DEFAULT_IGNORE_PATTERNS` (fnmatch on filepath; also matched against basename): lock files, common image/font binaries, minified/bundled JS/CSS/source maps, migrations, `*_generated.*`, `*.pb.go`, `**/generated/**`, `**/__snapshots__/**`. Binary files are also skipped via the `"Binary files … differ"` marker in the diff.
- Per-file diffs exceeding `MAX_DIFF_LINES_PER_FILE = 2000` are truncated with a notice.

## Entry Point

`codecurrents = codecurrents.cli:main` in `pyproject.toml`. There is no other public API — `summarize_repo(config)` in `core.py` is the natural programmatic entrypoint if you want to drive it from Python.

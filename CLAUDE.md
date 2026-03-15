# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CodeCurrents is a Python CLI tool that summarizes code changes from GitHub repositories using an LLM map-reduce architecture. It analyzes git commit diffs and produces high-level narrative summaries of architectural decisions and feature changes.

## Commands

```bash
# Install (editable, core deps only)
pip install -e .

# Install with all LLM backends (OpenAI, Gemini)
pip install -e ".[all]"

# Run
codecurrents --repo <url-or-local-path> --model <model> --since "7 days ago"

# Run with config file
codecurrents --repo <url> --config config.json

# Reprocess (ignore saved state)
codecurrents --repo <url> --since "7 days ago" --reprocess
```

There are no tests or linting commands configured.

## Architecture

The pipeline follows a **map-reduce** pattern orchestrated by `core.py:summarize_repo()`:

1. **Git ops** (`git_ops.py`) — Clone/fetch bare repos, extract commit metadata and diffs via subprocess (120s timeout)
2. **Filtering** (`filters.py`) — Skip low-signal commits (merges, dep bumps, renovate/dependabot) and ignore file patterns (lockfiles, migrations, minified assets). Single files >2000 lines are truncated.
3. **Chunking** (`chunker.py`) — Group commits into token-bounded chunks (~40k tokens default) using tiktoken (`cl100k_base`), with 1.2x safety margin for non-OpenAI models and binary-search truncation for oversized commits
4. **Map pass** (`llm.py`) — Summarize each chunk in parallel (3 workers via `ThreadPoolExecutor`)
5. **Reduce pass** (`llm.py`) — Synthesize chunk summaries into a final narrative
6. **Summary storage** (`summaries.py`) — Append run results as Markdown to `./summaries/<repo-slug>.md`
7. **State tracking** (`state.py`) — Persist last processed commit per repo as JSON in `./state/`

Multi-branch runs deduplicate commits by hash before sorting chronologically. Chunks or final summaries containing `NO_MAJOR_CHANGES` are handled as a special case.

### Multi-LLM Backend

Provider is detected by model name prefix: `claude-*` → Anthropic, `gpt-*`/`o1-*`/`o3-*`/`o4-*` → OpenAI, `gemini-*` → Google. If prefix is ambiguous, falls back by checking which API key is set. Default model is `claude-haiku-4-5-20251001`. LLM libraries are imported lazily. Retry logic uses exponential backoff (3 attempts). Temperature is 0.3, max_tokens is 2048.

### Configuration Precedence

CLI args > JSON config file > defaults in `config.py`. Custom prompts accept inline text or `.txt` file paths.

### Environment

API keys loaded from env vars (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`). Also loads from `~/.eve` or `~/.env` if present.

### Entry Point

CLI is registered as `codecurrents` via `codecurrents.cli:main` in `pyproject.toml`.

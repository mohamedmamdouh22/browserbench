# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run the full benchmark suite
uv run python run_browserbench.py --concurrency 3 --tasks 20

# Run with stealth disabled
uv run python run_browserbench.py --no-stealth --concurrency 3

# Use the smaller smoke-test CSV
uv run python run_browserbench.py --csv-file test_tasks.csv --tasks 5

# Run a single task for debugging
uv run python browser_test.py --task "Find the latest pricing for the Oculus Quest 3"
```

No test suite is configured — the project is a benchmark harness.

## Environment variables

```
INTUNED_STEALTH_CHROMIUM_PATH   Required. Path to the Intuned stealth Chromium binary.
OPENAI_API_KEY                  Required. LLM for the browser-use agent.
GROQ_API_KEY                    Required. Used by ChatGroq in browser_test.py.
```

Place in a `.env` file at the repo root; both scripts call `load_dotenv()` automatically.

## Architecture

BrowserBench benchmarks the Intuned stealth Chromium against autonomous web-browsing tasks using the `browser_use` library.

### Execution flow

1. **`run_browserbench.py`** — async orchestrator. Loads tasks from a CSV, fans them out with `asyncio.Semaphore`-bounded concurrency, and spawns each task as an isolated **subprocess**. Results are written incrementally to `results/browserbench_results_intuned.csv`; logs go to `logs/`.

2. **`browser_test.py`** — single-task harness. Called both directly (debugging) and by the subprocess runner. Creates a provider session via `intuned_provider`, connects `browser_use.Agent` via CDP, extracts `history.final_result()`, then calls `cleanup_session`. Returns `(final_message, session_url, execution_successful, error_message)`.

3. **`providers/intuned_provider.py`** — launches a local Chromium process with `--remote-debugging-port`, polls `/json/version` until DevTools is ready, and returns `(process, cdp_ws_url, user_data_dir)`. Cleanup kills the process and removes the temp profile directory.

### Subprocess isolation

Each task runs as a separate Python process so one crashed browser session cannot affect concurrent tasks. The child writes results to a temp JSON file (`task_id_{N}_result.json`) which the parent reads after the process exits.

### Task CSV format

Five required columns: `task_id`, `starting_url`, `task_description`, `ground_truth_url`, `ground_truth`.  
`browserbench.csv` is the full set; `test_tasks.csv` is a smaller smoke-test subset.

### Important: `success` column semantics

`success=True` means the agent run completed without crashes/exceptions — **not** that the agent produced a correct answer. Compare `agent_result` to `ground_truth` separately to evaluate correctness.

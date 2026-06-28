# BrowserBench

BrowserBench exercises the Intuned stealth Chromium against a shared set of autonomous web-browsing tasks. It launches isolated local browser sessions via CDP, runs a `browser_use` agent for each task, and emits timestamped CSV reports so you can track reliability and latency over time.

## Repository Layout

- `run_browserbench.py` – asynchronous benchmark runner; loads tasks from CSV, fans them out with bounded concurrency (each task is an isolated subprocess), and writes aggregated results.
- `browser_test.py` – single-task harness that launches a stealth Chromium session, runs the `browser_use` agent, captures the final message, and performs cleanup.
- `providers/intuned_provider.py` – adapter that launches the local Chromium binary with remote debugging, waits for DevTools to be ready, and returns the CDP WebSocket URL.
- `browserbench.csv` / `test_tasks.csv` – canonical and smoke-test task lists. Each row describes the start URL, natural-language instruction, and ground-truth expectation.
- `results/` – auto-created folder containing `browserbench_results_intuned[_no_stealth]_<timestamp>.csv` exports.
- `logs/` – per-task log files, one per run.
- `pyproject.toml` – project configuration and dependencies for uv.

## Prerequisites

### Installation with uv (Recommended)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
```

### Installation with pip

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Environment Variables

Copy `.env.template` to `.env` and fill in the values:

```bash
cp .env.template .env
```

```bash
# Path to the Intuned stealth Chromium binary (required)
INTUNED_STEALTH_CHROMIUM_PATH=/path/to/Chromium

# LLM API keys (required)
OPENAI_API_KEY=your_openai_api_key_here
GROQ_API_KEY=your_groq_api_key_here
```

## Running the Benchmark Suite

```bash
uv run python run_browserbench.py --concurrency 3 --tasks 20
```

Key flags:

- `--concurrency <int>` – number of simultaneous browser sessions (default: 3).
- `--tasks <int>` – limit the number of rows pulled from the CSV (default: all).
- `--csv-file <path>` – alternate task list (default: `browserbench.csv`).
- `--output <filename>` – custom name for the result CSV.
- `--no-stealth` – pass `stealth=False` to the provider.

Results are written incrementally to `results/browserbench_results_intuned.csv` as tasks complete. Each row includes task metadata, `success` status, `agent_result`, and `task_duration`.

## Running a Single Task

Use `browser_test.py` to debug a prompt or verify the provider setup:

```bash
uv run python browser_test.py --task "Find the latest pricing for the Oculus Quest 3"
```

This launches one Chromium session, runs the agent, prints the final answer, and cleans up automatically.

## How It Works

Each task runs as a separate subprocess so a crashed browser session cannot affect concurrent tasks. The provider:

1. Finds a free local port.
2. Launches the Intuned stealth Chromium with `--remote-debugging-port=<PORT>`.
3. Polls `http://localhost:<PORT>/json/version` until DevTools is ready.
4. Returns the CDP WebSocket URL to `browser_use`.
5. On cleanup, terminates the process and removes the temporary profile directory.

## Customising Task Sets

The task CSV expects five columns: `task_id`, `starting_url`, `task_description`, `ground_truth_url`, and `ground_truth`. Add rows or supply a different file via `--csv-file`. For quick smoke tests, use `test_tasks.csv`.

## Interpreting Results

The `success` column indicates whether the agent run **completed without technical errors**, not whether it produced the correct answer. To evaluate correctness, compare the `agent_result` column to `ground_truth` separately.

Failures are captured with the exception stored in `error_message`; the row still appears in the CSV so aggregate success rates remain accurate.

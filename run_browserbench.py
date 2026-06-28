#!/usr/bin/env python3
"""
Browser benchmark runner for the Intuned stealth Chromium.

Loads tasks from a CSV, fans them out with bounded concurrency (each task runs
as an isolated subprocess), and writes incremental results to a CSV report.

Output:
- Results: results/browserbench_results_intuned[_no_stealth].csv
- Per-task logs: logs/browserbench_results_intuned[_no_stealth]/task_id_{N}.log

Note: The 'success' column indicates whether the agent run completed without
technical errors, NOT whether the agent produced the correct answer. Compare
'agent_result' to 'ground_truth' separately to evaluate correctness.

Usage:
    python run_browserbench.py --concurrency 3 --tasks 10
    python run_browserbench.py --no-stealth --concurrency 3 --tasks 10
    python run_browserbench.py --csv-file test_tasks.csv
    python run_browserbench.py --help

Environment variables required:
    INTUNED_STEALTH_CHROMIUM_PATH  Path to the Intuned stealth Chromium binary
    OPENAI_API_KEY                 OpenAI API key for the browser-use agent
"""

import argparse
import asyncio
import csv
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def run_single_task_subprocess(
    stealth: bool,
    task_description: str,
    starting_url: str,
) -> tuple[Optional[str], Optional[str], Optional[bool], Optional[str]]:
    """Run a single browser task in-process. Called from subprocess entry point."""
    from browser_test import main as run_single_browser_task

    formatted_task = f"{task_description}. Begin task on the following url: {starting_url}"
    try:
        agent_result, session_url, is_successful, error_msg = await run_single_browser_task(
            stealth=stealth, task=formatted_task
        )
        return agent_result, session_url, is_successful, error_msg
    except Exception as e:
        return None, None, False, str(e)


@dataclass
class BenchmarkResult:
    """Benchmark result for a single task.

    Fields:
        success: True if the agent finished without crashes/exceptions.
                 Does NOT indicate answer correctness — compare agent_result
                 to ground_truth separately.
    """
    task_id: int
    starting_url: str
    task_description: str
    ground_truth_url: str
    ground_truth: str
    status: str
    session_url: Optional[str]
    launched_at: str
    agent_result: Optional[str]
    success: Optional[bool]
    error_message: Optional[str]
    task_duration: Optional[float]


class BrowserBenchmarkRunner:
    """Benchmark runner for the Intuned stealth Chromium."""

    FIELDNAMES = [
        "task_id", "starting_url", "task_description", "ground_truth_url",
        "ground_truth", "status", "session_url", "launched_at",
        "agent_result", "success", "error_message", "task_duration",
    ]

    def __init__(self, concurrency: int = 3, no_stealth: bool = False, output_file: Optional[str] = None):
        self.concurrency = concurrency
        self.no_stealth = no_stealth
        self.output_file = output_file
        self.results_dir = Path("results")
        self.results_dir.mkdir(exist_ok=True)
        self.logs_base_dir = Path("logs")
        self.logs_base_dir.mkdir(exist_ok=True)
        self._write_lock = asyncio.Lock()

    def load_tasks(self, csv_file: str, max_tasks: Optional[int] = None) -> List[Dict]:
        tasks = []
        with open(csv_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader, 1):
                if max_tasks and i > max_tasks:
                    break
                tasks.append({
                    "task_id": int(row["task_id"]),
                    "starting_url": row["starting_url"],
                    "task_description": row["task_description"],
                    "ground_truth_url": row["ground_truth_url"],
                    "ground_truth": row["ground_truth"],
                })
        return tasks

    def get_existing_task_ids(self, filepath: Path) -> set:
        if not filepath.exists():
            return set()
        existing_ids = set()
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if row.get("task_id"):
                        existing_ids.add(int(row["task_id"]))
            logger.info(f"Found {len(existing_ids)} existing tasks in {filepath}")
        except Exception as e:
            logger.error(f"Error reading existing task IDs: {e}")
        return existing_ids

    def initialize_output_file(self, filepath: Path):
        if not filepath.exists():
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=self.FIELDNAMES).writeheader()
            logger.info(f"Created output file: {filepath}")

    async def write_initial_task_row(self, filepath: Path, result: BenchmarkResult):
        async with self._write_lock:
            with open(filepath, "a", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=self.FIELDNAMES).writerow(asdict(result))

    async def update_task_row(self, filepath: Path, result: BenchmarkResult):
        async with self._write_lock:
            rows = []
            with open(filepath, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            updated = False
            for i, row in enumerate(rows):
                if int(row["task_id"]) == result.task_id:
                    rows[i] = asdict(result)
                    updated = True
                    break
            if not updated:
                rows.append(asdict(result))
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=self.FIELDNAMES)
                writer.writeheader()
                writer.writerows(rows)

    def get_output_filepath(self) -> Path:
        if self.output_file:
            return self.results_dir / self.output_file
        suffix = "_no_stealth" if self.no_stealth else ""
        return self.results_dir / f"browserbench_results_intuned{suffix}.csv"

    def get_log_directory(self, output_filepath: Path) -> Path:
        log_dir = self.logs_base_dir / output_filepath.stem
        log_dir.mkdir(exist_ok=True)
        return log_dir

    async def run_task_background(
        self, task: Dict, output_filepath: Path, log_directory: Path
    ) -> BenchmarkResult:
        launched_at = datetime.now().isoformat()
        start_time = datetime.now()
        stealth_enabled = not self.no_stealth

        result = BenchmarkResult(
            task_id=task["task_id"],
            starting_url=task["starting_url"],
            task_description=task["task_description"],
            ground_truth_url=task["ground_truth_url"],
            ground_truth=task["ground_truth"],
            status="running",
            session_url=None,
            launched_at=launched_at,
            agent_result=None,
            success=None,
            error_message=None,
            task_duration=None,
        )

        log_file = log_directory / f"task_id_{task['task_id']}.log"
        print(f"🚀 Launching task {task['task_id']}: {task['task_description'][:80]}...")

        try:
            await self.write_initial_task_row(output_filepath, result)

            result_file = log_directory / f"task_id_{task['task_id']}_result.json"
            cmd = [
                sys.executable,
                __file__,
                "--run-single-task",
                "--stealth" if stealth_enabled else "--no-stealth",
                "--task-description", task["task_description"],
                "--starting-url", task["starting_url"],
                "--result-file", str(result_file),
            ]

            with open(log_file, "w") as log_f:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=log_f,
                    stderr=asyncio.subprocess.STDOUT,
                    env=os.environ.copy(),
                )
                await process.wait()

            if result_file.exists():
                with open(result_file, "r") as f:
                    task_result = json.load(f)
                agent_result = task_result.get("agent_result")
                session_url = task_result.get("session_url")
                is_successful = task_result.get("is_successful")
                error_msg = task_result.get("error_msg")
                result_file.unlink()
            else:
                agent_result = None
                session_url = None
                is_successful = False
                error_msg = "Subprocess did not produce result file"

            duration = (datetime.now() - start_time).total_seconds()
            result.agent_result = agent_result or ""
            result.success = is_successful
            result.error_message = error_msg
            result.task_duration = duration
            result.status = "completed" if is_successful else "failed"
            result.session_url = session_url or ""

            await self.update_task_row(output_filepath, result)

            status_icon = "✅" if is_successful else "❌"
            print(f"{status_icon} Task {task['task_id']} {result.status} in {duration:.1f}s")

        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds()
            result.status = "failed"
            result.error_message = str(e)
            result.success = False
            result.task_duration = duration
            try:
                await self.update_task_row(output_filepath, result)
            except Exception as update_error:
                logger.error(f"Failed to update error status: {update_error}")
            print(f"❌ Task {task['task_id']} failed in {duration:.1f}s: {str(e)[:60]}")

        return result

    async def launch_benchmark(
        self, tasks: List[Dict], output_filepath: Path, max_new_tasks: Optional[int] = None
    ) -> List[BenchmarkResult]:
        self.initialize_output_file(output_filepath)
        log_directory = self.get_log_directory(output_filepath)
        logger.info(f"Logs will be written to: {log_directory}")

        existing_task_ids = self.get_existing_task_ids(output_filepath)
        new_tasks = [t for t in tasks if t["task_id"] not in existing_task_ids]
        if max_new_tasks is not None:
            new_tasks = new_tasks[:max_new_tasks]

        if not new_tasks:
            logger.info("All tasks already exist in output file, nothing to run")
            return []

        logger.info(f"Skipping {len(existing_task_ids)} existing tasks, running {len(new_tasks)} new tasks")

        stealth_mode = "enabled" if not self.no_stealth else "disabled"
        print(f"\n{'='*70}")
        print(f"🎯 Executing {len(new_tasks)} tasks with Intuned stealth Chromium")
        print(f"⚙️  Concurrency: {self.concurrency} workers | Stealth: {stealth_mode}")
        print(f"📁 Results: {output_filepath}")
        print(f"📝 Logs: {log_directory}/")
        print(f"{'='*70}\n")

        semaphore = asyncio.Semaphore(self.concurrency)

        async def run_with_semaphore(task: Dict) -> BenchmarkResult:
            async with semaphore:
                return await self.run_task_background(task, output_filepath, log_directory)

        background_tasks = []
        for task in new_tasks:
            bg_task = asyncio.create_task(run_with_semaphore(task))
            background_tasks.append(bg_task)
            await asyncio.sleep(0.5)

        results = await asyncio.gather(*background_tasks, return_exceptions=True)

        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Task {new_tasks[i]['task_id']} failed: {result}")
            else:
                final_results.append(result)

        print(f"\n{'='*70}")
        print("✨ All tasks completed!")
        print(f"{'='*70}\n")

        return final_results


def run_single_task_main(stealth: bool, task_description: str, starting_url: str, result_file: str) -> int:
    async def _run():
        try:
            agent_result, session_url, is_successful, error_msg = await run_single_task_subprocess(
                stealth=stealth,
                task_description=task_description,
                starting_url=starting_url,
            )
            result_data = {
                "agent_result": agent_result,
                "session_url": session_url,
                "is_successful": is_successful,
                "error_msg": error_msg,
            }
            with open(result_file, "w") as f:
                json.dump(result_data, f)
            return 0
        except Exception as e:
            with open(result_file, "w") as f:
                json.dump({"agent_result": None, "session_url": None, "is_successful": False, "error_msg": str(e)}, f)
            return 1

    return asyncio.run(_run())


def main():
    parser = argparse.ArgumentParser(description="Run browser benchmark with Intuned stealth Chromium")

    # Hidden subprocess arguments
    parser.add_argument("--run-single-task", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--task-description", type=str, help=argparse.SUPPRESS)
    parser.add_argument("--starting-url", type=str, help=argparse.SUPPRESS)
    parser.add_argument("--result-file", type=str, help=argparse.SUPPRESS)
    parser.add_argument("--stealth", action="store_true", help=argparse.SUPPRESS)

    parser.add_argument("--concurrency", type=int, default=3, help="Concurrent browser sessions (default: 3)")
    parser.add_argument("--tasks", type=int, default=None, help="Max tasks to run (default: all)")
    parser.add_argument("--csv-file", type=str, default="browserbench.csv", help="Task CSV file (default: browserbench.csv)")
    parser.add_argument("--output", type=str, default=None, help="Output CSV filename")
    parser.add_argument("--no-stealth", action="store_true", help="Disable stealth mode (enabled by default)")

    args = parser.parse_args()

    if args.run_single_task:
        return run_single_task_main(
            stealth=args.stealth,
            task_description=args.task_description,
            starting_url=args.starting_url,
            result_file=args.result_file,
        )

    if not os.getenv("INTUNED_STEALTH_CHROMIUM_PATH"):
        logger.error("INTUNED_STEALTH_CHROMIUM_PATH environment variable is required")
        return 1

    if not os.getenv("OPENAI_API_KEY"):
        logger.error("OPENAI_API_KEY environment variable is required")
        return 1

    runner = BrowserBenchmarkRunner(
        concurrency=args.concurrency,
        no_stealth=args.no_stealth,
        output_file=args.output,
    )

    try:
        tasks = runner.load_tasks(args.csv_file)
        logger.info(f"Loaded {len(tasks)} tasks from {args.csv_file}")
    except Exception as e:
        logger.error(f"Error loading tasks: {e}")
        return 1

    if not tasks:
        logger.error("No tasks loaded")
        return 1

    output_filepath = runner.get_output_filepath()
    logger.info(f"Output will be written to: {output_filepath}")

    try:
        results = asyncio.run(runner.launch_benchmark(tasks, output_filepath, max_new_tasks=args.tasks))
        logger.info(f"Completed {len(results)} tasks")
    except Exception as e:
        logger.error(f"Error running benchmark: {e}")
        return 1

    if results:
        completed = sum(1 for r in results if r.status == "completed")
        failed = sum(1 for r in results if r.status == "failed")
        total_duration = sum(r.task_duration for r in results if r.task_duration)
        avg_duration = total_duration / len(results) if results else 0
        success_rate = (completed / len(results) * 100) if results else 0
        log_directory = runner.get_log_directory(output_filepath)

        print("📊 Benchmark Summary")
        print("─" * 70)
        print(f"Provider:        Intuned stealth Chromium")
        print(f"Tasks run:       {len(results)}")
        print(f"✅ Successful:   {completed}")
        print(f"❌ Failed:       {failed}")
        print(f"Success rate:    {success_rate:.1f}%")
        print(f"Total time:      {total_duration:.1f}s")
        print(f"Avg per task:    {avg_duration:.1f}s")
        print(f"\n📁 Results:      {output_filepath}")
        print(f"📝 Logs:         {log_directory}/")
        print(f"{'─'*70}\n")

    return 0


if __name__ == "__main__":
    exit(main())

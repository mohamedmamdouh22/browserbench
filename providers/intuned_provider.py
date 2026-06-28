"""
Intuned stealth Chromium provider.
Launches a local Chromium process with remote debugging enabled and returns
the WebSocket CDP URL for browser_use to connect to.

Requires INTUNED_STEALTH_CHROMIUM_PATH environment variable.
"""

import os
import shutil
import socket
import subprocess
import tempfile
import time

import requests


def find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def create_session(stealth: bool = True):
    """Launch an Intuned stealth Chromium instance and return the CDP WebSocket URL.

    Args:
        stealth: Unused — the binary is always the stealth build. Kept for interface parity.

    Returns:
        tuple: (process, cdp_ws_url, user_data_dir)

    Raises:
        ValueError: If INTUNED_STEALTH_CHROMIUM_PATH is not set.
        FileNotFoundError: If the Chromium binary does not exist at the given path.
        RuntimeError: If Chromium does not expose DevTools within the timeout.
    """
    chromium_path = os.environ.get("INTUNED_STEALTH_CHROMIUM_PATH")
    if not chromium_path:
        raise ValueError(
            "INTUNED_STEALTH_CHROMIUM_PATH environment variable is required"
        )

    if not os.path.exists(chromium_path):
        raise FileNotFoundError(
            f"Intuned stealth Chromium not found at: {chromium_path}"
        )

    port = find_free_port()
    user_data_dir = tempfile.mkdtemp(prefix="intuned_bench_")

    cmd = [
        chromium_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
    ]

    process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"Launched Intuned Chromium (pid={process.pid}) on port {port}")

    # Poll until DevTools endpoint is ready (up to 20 seconds)
    cdp_ws_url = None
    for _ in range(40):
        try:
            resp = requests.get(f"http://localhost:{port}/json/version", timeout=2)
            if resp.ok:
                cdp_ws_url = resp.json()["webSocketDebuggerUrl"]
                break
        except Exception:
            pass
        time.sleep(0.5)

    if cdp_ws_url is None:
        process.kill()
        shutil.rmtree(user_data_dir, ignore_errors=True)
        raise RuntimeError(
            f"Intuned Chromium (pid={process.pid}) did not expose DevTools on port {port} within 20s"
        )

    print(f"Intuned Chromium ready — CDP: {cdp_ws_url}")
    return process, cdp_ws_url, user_data_dir


def cleanup_session(process, user_data_dir: str | None = None):
    """Terminate the Chromium process and remove its temporary profile directory."""
    try:
        process.terminate()
        process.wait(timeout=10)
    except Exception:
        process.kill()

    if user_data_dir:
        shutil.rmtree(user_data_dir, ignore_errors=True)

    return None  # No remote session URL for a local browser

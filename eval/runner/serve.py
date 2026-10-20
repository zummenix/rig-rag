"""Serve lifecycle: start `rig-rag serve`, wait for health, tear it down.

The server is one long-lived process per profile. It is pointed at a
non-existent site directory so only the JSON API is served, and its stdout /
stderr are captured to the run directory for debugging.
"""

from __future__ import annotations

import contextlib
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

from eval.runner.httpquery import QueryClient
from eval.runner.rigrag import RigRag


def free_port() -> int:
    """A currently-free loopback port (best effort; binds then releases)."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def running_server(
    rig: RigRag,
    config: Path,
    port: int,
    log_path: Path,
    startup_timeout: float = 90.0,
    poll_interval: float = 0.25,
) -> Iterator[QueryClient]:
    """Context manager yielding a healthy [`QueryClient`] for one server."""

    host = "127.0.0.1"
    site_dir = log_path.parent / "no-site"
    command = rig.serve_command(config, f"{host}:{port}", site_dir)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("w", encoding="utf-8")
    log.write(f"$ {' '.join(command)}\n")
    log.flush()
    process = subprocess.Popen(command, cwd=rig.cwd, stdout=log, stderr=subprocess.STDOUT)

    client = QueryClient(host, port)
    try:
        deadline = time.monotonic() + startup_timeout
        while True:
            if process.poll() is not None:
                raise RuntimeError(
                    f"serve exited early with code {process.returncode}; see {log_path}"
                )
            if client.health():
                break
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"serve did not become healthy within {startup_timeout:.0f}s; "
                    f"see {log_path}"
                )
            time.sleep(poll_interval)

        yield client
    finally:
        client.close()
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        log.close()

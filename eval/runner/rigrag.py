"""Thin wrappers around the `rig-rag` binary.

The runner drives the real `ingest` / `promote` / `serve` code paths (no
reimplementation) exactly as a user would from the command line.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


class RigRagError(RuntimeError):
    pass


class RigRag:
    """Runs `rig-rag` subcommands against profile config/sources files."""

    def __init__(self, binary: Path, cwd: Path) -> None:
        self.binary = Path(binary)
        self.cwd = Path(cwd)

    def _command(self, *args: str) -> list[str]:
        return [str(self.binary), *args]

    def _run(self, *args: str) -> None:
        result = subprocess.run(self._command(*args), cwd=self.cwd, check=False)
        if result.returncode != 0:
            raise RigRagError(
                f"rig-rag {' '.join(args)} failed with exit code {result.returncode}"
            )

    def _run_capture(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            self._command(*args), cwd=self.cwd, check=False, capture_output=True, text=True
        )

    def ingest(
        self, config: Path, sources: Path, report: Path, force: bool = False
    ) -> dict:
        """Ingests a profile and returns the parsed `--report` document."""

        args = [
            "--config",
            str(config),
            "--sources",
            str(sources),
            "ingest",
            "--report",
            str(report),
        ]
        if force:
            args.append("--force")
        self._run(*args)
        return json.loads(report.read_text(encoding="utf-8"))

    def promote(self, config: Path, collection: str) -> None:
        self._run("--config", str(config), "promote", collection)

    def serve_command(self, config: Path, bind: str, site_dir: Path) -> list[str]:
        return self._command(
            "--config",
            str(config),
            "serve",
            "--bind",
            bind,
            "--site-dir",
            str(site_dir),
        )

    def model(self, config: Path) -> str:
        result = self._run_capture("--config", str(config), "model")
        if result.returncode != 0:
            raise RigRagError(f"rig-rag model failed: {result.stderr.strip()}")
        return result.stdout.strip()


def resolve_binary(repo_root: Path, override: str | None = None, build: bool = True) -> Path:
    """Finds the `rig-rag` binary, preferring the most recently built one.

    An out-of-date `target/release/rig-rag` can silently lack newer flags (for
    example `ingest --report`), so the newest of `target/{release,debug}` wins
    rather than a fixed profile order. An explicit `--bin` always overrides.
    """

    if override:
        binary = Path(override)
        if not binary.is_file():
            raise FileNotFoundError(f"--bin {binary} does not exist")
        return binary.resolve()

    repo_root = Path(repo_root)
    candidates = [
        candidate
        for candidate in (
            repo_root / "target" / "release" / "rig-rag",
            repo_root / "target" / "debug" / "rig-rag",
        )
        if candidate.is_file()
    ]
    if candidates:
        return max(candidates, key=lambda path: path.stat().st_mtime).resolve()
    if not build:
        raise FileNotFoundError(
            "no built rig-rag binary found under target/{release,debug}; run `cargo build`"
        )
    subprocess.run(
        ["cargo", "build", "--bin", "rig-rag"], cwd=repo_root, check=True
    )
    return (repo_root / "target" / "debug" / "rig-rag").resolve()

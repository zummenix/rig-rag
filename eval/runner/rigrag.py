"""Thin wrappers around the `rig-rag` binary.

The runner drives the real `ingest` / `promote` / `serve` code paths (no
reimplementation) exactly as a user would from the command line.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from eval.runner.paths import repo_relative


class RigRagError(RuntimeError):
    pass


class RigRag:
    """Runs `rig-rag` subcommands against profile config/sources files."""

    def __init__(self, binary: Path, cwd: Path) -> None:
        self.binary = Path(binary)
        self.cwd = Path(cwd)

    def _command(self, *args: str) -> list[str]:
        return [str(self.binary), *args]

    def _cli_path(self, path: Path) -> str:
        """A path argument, relative to `self.cwd` when it is under it.

        The binary records `--config`/`--sources` verbatim in its ingest report;
        passing repo-relative paths keeps that committed artifact portable even
        though the runner works with absolute paths internally.
        """

        return str(repo_relative(path, self.cwd))

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
            self._cli_path(config),
            "--sources",
            self._cli_path(sources),
            "ingest",
            "--report",
            self._cli_path(report),
        ]
        if force:
            args.append("--force")
        self._run(*args)
        return json.loads(report.read_text(encoding="utf-8"))

    def promote(self, config: Path, collection: str) -> None:
        self._run("--config", self._cli_path(config), "promote", collection)

    def serve_command(self, config: Path, bind: str, site_dir: Path) -> list[str]:
        return self._command(
            "--config",
            self._cli_path(config),
            "serve",
            "--bind",
            bind,
            "--site-dir",
            self._cli_path(site_dir),
        )

    def model(self, config: Path) -> str:
        result = self._run_capture("--config", self._cli_path(config), "model")
        if result.returncode != 0:
            raise RigRagError(f"rig-rag model failed: {result.stderr.strip()}")
        return result.stdout.strip()


def resolve_binary(repo_root: Path, override: str | None = None) -> Path:
    """Returns the `rig-rag` binary to evaluate.

    An explicit `--bin` is used verbatim: it is the caller's responsibility that
    it matches the sources under test, and its hash is recorded in the results.
    With no override the binary is **built now** with `cargo build --release`, so
    a stale `target/` cannot be measured silently; Cargo's incremental build
    makes this cheap when nothing changed.
    """

    if override:
        binary = Path(override)
        if not binary.is_file():
            raise FileNotFoundError(f"--bin {binary} does not exist")
        return binary.resolve()

    repo_root = Path(repo_root)
    subprocess.run(
        ["cargo", "build", "--release", "--bin", "rig-rag"],
        cwd=repo_root,
        check=True,
    )
    return (repo_root / "target" / "release" / "rig-rag").resolve()


def binary_sha256(binary: Path) -> str:
    """Content hash of the evaluated binary, for portable provenance."""

    digest = hashlib.sha256()
    with Path(binary).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

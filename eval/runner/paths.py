"""Path helpers shared by the eval runner.

Committed artifacts (`results.json`, `ingest-*.json`) must describe a run
portably, so host-specific absolute paths are reduced to repository-relative
ones wherever the target lives under the repository root.
"""

from __future__ import annotations

from pathlib import Path


def repo_relative(path: str | Path, root: str | Path) -> Path:
    """`path` relative to `root` when it is under it, else `path` unchanged.

    Relative inputs are returned as-is; the runner always spawns subprocesses
    with `cwd=repo_root`, so a relative path is already interpreted correctly.
    """

    path = Path(path)
    if not path.is_absolute():
        return path
    try:
        return path.relative_to(Path(root))
    except ValueError:
        return path

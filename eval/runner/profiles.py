"""Corpus profiles: `eval/profiles/<name>/{rig-rag.toml,sources.json}`.

A profile is a pair of the production config and sources files. The runner
drives the real binary against them, so this module only discovers and parses
them; it never rewrites anything (that is `rig-rag promote`'s job).
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

PROFILES_DIR = "eval/profiles"


@dataclass(frozen=True)
class Profile:
    """A parsed corpus profile, with the raw files it came from."""

    name: str
    config_path: Path
    sources_path: Path
    config: dict
    sources: list[dict]

    @property
    def data_root(self) -> str:
        return self.config.get("corpus", {}).get("data_root", "data")

    @property
    def prefix(self) -> str:
        return self.config.get("corpus", {}).get("prefix", "docs")

    @property
    def active_collection(self) -> str:
        return self.config.get("collection", {}).get("active", "")

    def source_names(self) -> set[str]:
        return {str(source["name"]) for source in self.sources}


def parse_profile(
    name: str, config_path: str | Path, sources_path: str | Path
) -> Profile:
    config_path = Path(config_path)
    sources_path = Path(sources_path)
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    if not isinstance(sources, list):
        raise ValueError(f"{sources_path} must be a JSON array of sources")
    return Profile(
        name=name,
        config_path=config_path,
        sources_path=sources_path,
        config=config,
        sources=sources,
    )


def discover_profiles(
    repo_root: str | Path, selected: list[str] | None = None
) -> list[Profile]:
    """Finds profiles under `eval/profiles`, filtered/ordered by `selected`."""

    repo_root = Path(repo_root)
    base = repo_root / PROFILES_DIR
    if not base.is_dir():
        raise FileNotFoundError(f"no profiles directory at {base}")

    discovered: dict[str, Profile] = {}
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        config_path = entry / "rig-rag.toml"
        sources_path = entry / "sources.json"
        if config_path.is_file() and sources_path.is_file():
            discovered[entry.name] = parse_profile(entry.name, config_path, sources_path)

    if selected:
        missing = [name for name in selected if name not in discovered]
        if missing:
            raise ValueError(
                f"unknown profile(s) {missing}; available: {sorted(discovered)}"
            )
        return [discovered[name] for name in selected]
    return [discovered[name] for name in sorted(discovered)]

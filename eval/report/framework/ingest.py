"""Version-aware loader for the Rust `ingest --report` JSON.

The ingestion artifact (`eval/results/<experiment>/<run>/ingest-<profile>.json`)
is the second report part and, like `results.json`, a versioned contract of its
own (see `src/report.rs`). It advances on a different schedule and is loaded and
checked on its own terms with `INGEST_SUPPORTED_SCHEMA_VERSIONS`, so the
framework can render ingest-only, retrieval-only, and combined documents.

A `reused` report is a special case the renderer must not misread: the
collection already existed, so the run gathered nothing and every count, token,
and timing is zero **because it was not measured**, not because the cost was
free. `cost_measured` distinguishes the two.
"""

from __future__ import annotations

import json
from pathlib import Path

# Every ingest-report schema version this framework renders. Append when
# `report::SCHEMA_VERSION` advances and rendering support lands; never reorder.
INGEST_SUPPORTED_SCHEMA_VERSIONS: tuple[int, ...] = (1,)
INGEST_LATEST_SCHEMA_VERSION: int = max(INGEST_SUPPORTED_SCHEMA_VERSIONS)

REUSED = "reused"


class IngestError(Exception):
    """An ingest report is missing, malformed, or missing a required field."""


class UnsupportedIngestSchemaVersion(IngestError):
    """`ingest-<profile>.json` carries a schema version this framework cannot render."""

    def __init__(self, version: object, *, source: str = "ingest.json") -> None:
        known = ", ".join(str(item) for item in INGEST_SUPPORTED_SCHEMA_VERSIONS)
        relation = (
            "newer than"
            if isinstance(version, int) and version > INGEST_LATEST_SCHEMA_VERSION
            else "older than"
        )
        super().__init__(
            f"{source}: schema_version {version!r} is {relation} this report framework "
            f"supports (known versions: {known}). Update eval/report/framework, or check "
            "out a revision that knows this version."
        )
        self.version = version


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_ingest(document: object, *, source: str = "ingest.json") -> int:
    """Checks the top-level shape and supported version; returns the version."""

    if not isinstance(document, dict):
        raise IngestError(f"{source}: expected a JSON object, got {type(document).__name__}")

    version = document.get("schema_version")
    if not _is_int(version):
        raise IngestError(f"{source}: missing or non-integer schema_version")
    if version not in INGEST_SUPPORTED_SCHEMA_VERSIONS:
        raise UnsupportedIngestSchemaVersion(version, source=source)
    if not isinstance(document.get("totals"), dict):
        raise IngestError(f"{source}: missing `totals` object")
    return version


def load_ingest(path: str | Path) -> dict:
    """Reads and validates an `ingest-<profile>.json` file."""

    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise IngestError(f"ingest report not found: {path}") from None
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise IngestError(f"{path} is not valid JSON: {error}") from None
    validate_ingest(document, source=str(path))
    return document


def cost_measured(ingest: dict) -> bool:
    """Whether the run actually gathered cost data.

    A `reused` collection short-circuits ingest before any counting or timing,
    so its report must be shown as "cost not measured" rather than as zeros.
    """

    return str(ingest.get("status", "")) != REUSED


def profile_label(ingest: dict, *, fallback: str = "ingest") -> str:
    """A human label for one ingest report: its profile, else its collection."""

    for key in ("profile", "collection"):
        value = ingest.get(key)
        if value:
            return str(value)
    return fallback

"""Retrieval-eval assets and Python runner for `rig-rag`.

See `docs/eval-plan.md` for the design. The Rust binary owns prod behavior and
the ingest report; everything in this package is orchestration, metrics, and
reporting layered on top of the real `ingest`/`promote`/`serve` code paths.
"""

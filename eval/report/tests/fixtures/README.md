# Report-framework fixtures

Small, committed `results.json` documents that pin the shapes the report
framework must keep rendering. They are *render* inputs, not experiment
results: nothing here is a frozen baseline.

- `v1/results.json` — the current `schema_version: 1` shape (records
  `k_values` and `commit_ref`).
- `v1_legacy/results.json` — the older v1 shape produced before those fields
  existed (no `k_values`, no `commit_ref`, an absolute `environment.binary`).
  The loader derives the swept k set and the latency k from it, so an artifact
  measured by an older runner still renders.

`schemas:` `v1` is the only supported version today; `LATEST_SCHEMA_VERSION` in
`eval/report/framework/loader.py` tracks it. A newer file (e.g.
`schema_version: 2`) is refused with a clear message until support is added.

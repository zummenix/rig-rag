# Report-framework fixtures

Small, committed `results.json` documents that pin the shapes the report
framework must keep rendering. They are *render* inputs, not experiment
results: nothing here is a frozen baseline.

- `v2/results.json` — the supported `schema_version: 2` retrieval shape:
  recorded `k_values` (3/7/20), per-profile `timings` (search/rerank/e2e), a
  per-call `rerank` rollup, and per-question `timings` + `rerank` entries.

`schemas:` `v2` is the only supported version today; `LATEST_SCHEMA_VERSION` in
`eval/report/framework/loader.py` tracks it. Version 1 predates the rerank-cache
removal and is refused with a clear message: its latency samples mixed warm
cache hits with uncached provider calls, so it is not comparable.

The `ingest/` fixtures are a separate, independently-versioned contract.

# Report-framework fixtures

Small, committed `results.json` documents that pin the shapes the report
framework must keep rendering. They are *render* inputs, not experiment
results: nothing here is a frozen baseline.

- `v3/results.json` — the current `schema_version: 3` retrieval shape: a
  top-level `modes` list (`hybrid`, `dense`, `sparse`), recorded `k_values`
  (3/7/20), one `modes` map per profile with per-mode `timings`
  (search/rerank/e2e) and a per-mode `rerank` rollup, and per-question `modes`
  entries carrying `by_k`, `timings`, and `rerank`. Only the max-k `by_k` entry
  holds the ranked `hits` list; smaller-k metrics are exact prefixes.
- `v2/results.json` — the previous `schema_version: 2` shape. It predates the
  `mode` field but is otherwise identical, so the loader keeps rendering it as
  a single `unknown` series.

`schemas:` `v2` and `v3` are supported; `LATEST_SCHEMA_VERSION` in
`eval/report/framework/loader.py` tracks the newest. Version 1 predates the
rerank-cache removal and is refused with a clear message: its latency samples
mixed warm cache hits with uncached provider calls, so it is not comparable.

The `ingest/` fixtures are a separate, independently-versioned contract.

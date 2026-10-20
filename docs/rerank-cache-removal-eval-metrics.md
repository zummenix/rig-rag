# Remove rerank score cache, rewrite eval metrics

Status: planned (not started)
Scope: Rust `serve` path + Python eval runner/report. Old `results.json` v1
baselines are invalidated by design; a new v2 baseline will be recorded in a
follow-up session, not in this change.

## Motivation

`Reranker` keeps a `Mutex<ScoreCache>` behind `serve --cache-rerank` so the eval
can sweep several `k` values (and latency reps) while paying for the rerank
provider only once per question. That cache has three costs:

1. **Correctness-ish coupling**: eval results depend on cache behavior, and
   latency samples are a mix of warm cache hits and cold provider calls.
2. **Complexity**: `canonical_order`/`to_original` and the SHA-256 cache key
   exist only to make cache lookups stable despite Qdrant's unstable fused
   order.
3. **Hidden measurement**: cache hits never reach the provider and never log,
   so the report cannot attribute rerank cost/duration per question.

The replacement measures the real thing: one uncached query per question,
per-call timings logged by the server, and a report that separates search
(local) from rerank (paid network) from end-to-end latency.

## Decisions locked in during design review

| Topic | Decision |
| --- | --- |
| Cache | Clean delete, no deprecation alias, no `Option<ScoreCache>` left |
| `serve` flag | `--log-eval-metrics` (bool, serve-only; not in `rig-rag.toml`) |
| Eval runner | Always passes `--log-eval-metrics`; no runner flag, no toggle |
| Rerank log | Always emitted when the flag is on; `status=success|failed` |
| Search log | Emitted when the flag is on, immediately after the fused response is parsed, before reranking; duration = embed + Qdrant only |
| Correlation | `q={first 16 hex of sha256(question)}` on both lines; eval joins post-run per profile |
| Hash collisions | Per-profile join, plus an assertion that question texts are unique per profile |
| Queries per question | Exactly one, at `k = max(selected k)`; all metrics derived by prefix-truncating the ranked list |
| Warmups / reps | Removed entirely (samples are cold by definition) |
| k grid | `{3, 7, 20}`, default all three; `--k` still selectable |
| `--latency-k` | Removed; no special reporting k anywhere |
| Summary/degradation | Full metrics × k: recall, purity, MRR, no-hit all per k |
| Rerank failure | Record and report, do not fail the run |
| Report timing | One "Timing" section: three horizontal grouped-bar charts (search, rerank, total/e2e), p50/p95 per profile, then aggregate table + rerank cost table |
| Old baselines | Leave artifacts and experiment pins untouched; loader refuses v1 |
| Baseline recording | Out of scope for this change |

## Why one query at `k=20` is equivalent to the old sweep

With `[reranker].candidates = 30` and `k ∈ {3, 7, 20}`:

- `Store::search` requests `max(k, candidates) = 30` fused hits for every one of
  those k values (`src/store.rs:228-231`).
- The reranker scores all 30, `select_reranked` orders them and cuts to `k`
  (`src/store.rs:268-290`).
- So the ranked lists for `k=3` and `k=7` are exact prefixes of the `k=20`
  list. Truncation is not an approximation.

Caveat to encode in the plan: this equivalence assumes `candidates >= max(k)`.
The eval profiles use the default 30 (`eval/profiles/*/rig-rag.toml`), so it
holds. Document it next to the truncation code.

## 1. Rust: delete the cache

### `src/reranker.rs`

- Remove `use std::collections::{HashMap, VecDeque}`, `std::sync::Mutex`,
  `CACHE_CAPACITY`, `struct ScoreCache` and its `impl`, field
  `cache: Option<Mutex<ScoreCache>>`, `cached()`.
- Remove `canonical_order` / `to_original` and the canonicalized request. The
  only reason documents were sorted before sending was cache-key stability.
  Send `documents` in fused order; provider indexes then map directly.
- Keep `sha2` (now used for the question hash) and `HashSet` (used by `rank`).

### Signatures

- `Reranker::from_config(config: &RerankerConfig, log_eval_metrics: bool)`.
- `Reranker::new(..., timeout, log_eval_metrics)` (private, used by tests).
- `Store::connect(config: &Config, log_eval_metrics: bool)`
  (`src/store.rs:115`).
- `prod::QdrantRetriever::connect(config: &Config, log_eval_metrics: bool)`
  (`src/serve/prod.rs:24`).
- `ServeArgs { ..., log_eval_metrics: bool }` (`src/serve/mod.rs:39-44`) and the
  clap `serve` field (`src/main.rs:39-49`, dispatch at `:75-85`).

### `src/serve/mod.rs`

- Replace the `--cache-rerank` warning block (`:66-68`) with nothing (nothing to
  warn about for a logging flag).
- Banner (`:73-77`): print `cache` → `eval metrics: on|off`.
- `--log-eval-metrics` on a config without `[reranker]` is harmless: `Store`
  still logs `search` lines; no `rerank` lines appear.

## 2. Rust: structured eval-metrics logging

Create a small module `src/eval_metrics.rs` (shared by `Store` and `Reranker`):

```rust
/// First 16 lowercase hex chars of sha256(question), UTF-8, no normalization.
pub fn question_hash(question: &str) -> String;

pub fn log_search(question: &str, k: u64, candidates: usize, elapsed: Duration);
pub fn log_rerank(
    question: &str,
    model: &str,
    documents: usize,
    results: usize,
    elapsed: Duration,
    status: RerankStatus,       // Success | Failed
    usage: Option<&Usage>,
    error: Option<&str>,
);
```

Formatting rules:

- `q=<hash>` is always the first field after the prefix.
- Durations are integer milliseconds via `as_millis()` (matches today's rerank
  line and keeps the Python parser simple).
- Lines go to `eprintln!` so they land in the captured `serve-<profile>.log`
  (`eval/runner/serve.py:30-49` redirects stderr into the log).

Exact line shapes:

```text
search q=1f3a2b4c5d6e7f80 k=20 candidates=30 duration_ms=412
rerank q=1f3a2b4c5d6e7f80 model=rerank-3 docs=30 results=30 duration_ms=1188 status=success tokens=39 cost=0.000351 cost_rub=0.03
rerank q=1f3a2b4c5d6e7f80 model=rerank-3 docs=30 results=0 duration_ms=2001 status=failed error=rerank endpoint returned 500: ...
```

- `status` is written before the optional `tokens`/`cost`/`cost_rub` so it is
  always in a fixed position.
- On failure, `results=0`, `duration_ms` = time until failure, and `error=` is
  the last field. The parser reads everything after `error=` as the value
  (the message may contain spaces).
- On success with usage absent, the usage fields are omitted exactly as today.
- `docs` is the number of documents sent; `results` is the number returned by
  `rank()`.
- `search` has no `status` (it either produced hits or the request failed, in
  which case there is no line and eval fails that question loudly).

### `Reranker::rerank` refactor

Split the provider call into an inner function and log in the outer one, so the
failure path is logged once with a measured duration:

```rust
pub async fn rerank(&self, question: &str, documents: &[String]) -> Result<Vec<Ranked>> {
    let started = Instant::now();
    match self.score(question, documents).await {
        Ok((ranked, usage)) => {
            if self.log_eval_metrics {
                eval_metrics::log_rerank(question, &self.model, documents.len(),
                    ranked.len(), started.elapsed(), Success, usage.as_ref(), None);
            }
            Ok(ranked)
        }
        Err(error) => {
            if self.log_eval_metrics {
                eval_metrics::log_rerank(question, &self.model, documents.len(),
                    0, started.elapsed(), Failed, None, Some(&format!("{error:#}")));
            } else {
                // Preserve the operator-visible warning when metrics are off.
                eprintln!("serve: rerank failed (falling back to fusion): {error:#}");
            }
            Err(error)
        }
    }
}
```

`Store::search` then only falls back, without logging:

```rust
if let Some(reranker) = &self.reranker {
    let documents = ...;
    match reranker.rerank(question, &documents).await {
        Ok(ranked) => return Ok(select_reranked(hits, &ranked, k)),
        Err(_) => {}          // already logged by Reranker
    }
}
```

### `Store::search` timing point

```rust
let started = Instant::now();
// embed + build prefetches + Qdrant query + parse payloads
let hits = ...;
if self.log_eval_metrics {
    eval_metrics::log_search(question, k, hits.len(), started.elapsed());
}
// rerank / cut / return
```

- `candidates = hits.len()` (fused hits after `score_threshold`, can be less than
  `limit`).
- The line is emitted on both the rerank-success and rerank-failure paths, and
  when no reranker is configured.
- No line when the query itself errors; the eval request fails and the run
  surfaces it.
- The ingest path never calls `search`, so no ingest noise.

### `Store` struct

Add `log_eval_metrics: bool` next to `reranker`. `Store::new` (ingest) sets it
to `false`; `Store::connect` takes the flag. `Reranker` keeps its own copy
(passed through `from_config`).

## 3. Python eval runner

### 3.1 CLI (`eval/runner/__main__.py`)

Delete: `--cache-rerank` (`:60-64`), `--warmup` (`:84`), `--reps` (`:85`),
`--latency-k` (`:87-91`).

Change `--k` choices/default: `{3, 7, 20}`, default `"3,7,20"`; `_parse_k`
(`:17-35`) rejects anything outside that grid.

`RunOptions` (`eval/runner/run.py:32-46`) loses `warmup`, `reps`,
`latency_k`, `cache_rerank`; keeps `k_values`.

`eval/runner/metrics.py`: `K_VALUES = (3, 7, 20)`; delete `LATENCY_K`.

`eval/runner/rigrag.py:75-89` + `eval/runner/serve.py:30-49`: drop
`cache_rerank`; always append `--log-eval-metrics` to the serve command.

### 3.2 Hash join (`eval/runner/serve_log.py` or a new `eval/runner/eval_log.py`)

Add:

```python
def question_hash(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]

def parse_log(log_path: Path) -> dict[str, dict]:
    # {hash: {"search": {...}, "rerank": {...}}}
```

`parse_log` reads `search ` and `rerank ` prefixed lines, parses key=value
fields (the existing `_number` helper is reused), uses `error=` as the
remainder of the line, and keeps the last occurrence for a hash (there should
be exactly one each; a duplicate is a warning-worthy anomaly).

Keep `rerank_stats()` but re-key everything by hash and derive:

- `calls` = number of `rerank` lines; `failures` = lines with `status=failed`.
- `documents`, `tokens`, `cost`, `cost_rub` summed over all lines (failed lines
  contribute `docs` and `results=0`).
- `duration_ms` list includes both statuses (it is real paid time either way).

Add `search_stats()` with the same aggregate math (`calls` = search lines,
`duration_ms`, p50/p95; no usage fields).

### 3.3 `_sweep` → one query per question (`eval/runner/run.py:134-202`)

```python
query_k = max(options.k_values)
for question in questions:
    hits, e2e_ms = client.query(question.question, query_k)
    by_k = {str(k): evaluate_at_k(hits[:k], question, profile.data_root)
            for k in options.k_values}
    for k in options.k_values:            # hits.jsonl keeps the per-k view
        for rank, hit in enumerate(hits[:k], start=1):
            options.hits.append(_hit_record(..., k, rank, hit, ...))
```

- `evaluate_at_k` already takes the hit list and measures "returned list", so
  prefix truncation needs no metric changes.
- Per question record becomes:

  ```python
  {"id", "question", "answerable", "gold", "notes", "by_k",
   "timings": {"e2e_ms", "search_ms", "rerank_ms"},
   "rerank": {"docs", "results", "tokens", "cost", "cost_rub", "status"}}
  ```

  `search_ms`/`rerank_ms` and the rerank entry are `None` when the flag somehow
  produced no line; `status == "failed"` ⇒ `rerank_ms` still present (measured
  until failure).
- After `running_server` exits, parse the log and join:

  ```python
  parsed = parse_log(log_path)                  # per profile
  by_hash = {question_hash(q.question): q for q in applicable}
  assert len(by_hash) == len(applicable)        # uniqueness per profile
  ```
  Attach `timings`/`rerank` per question by its hash.
- Per-profile `timings` block:

  ```jsonc
  "timings": {
    "search": {"samples_ms": [...], "p50_ms": ..., "p95_ms": ..., "mean_ms": ..., "sum_ms": ...},
    "rerank": {...},
    "e2e":    {...}
  }
  ```

  p50/p95 are across questions (one sample per question). Make that explicit in
  the `results.py` docstring so nobody reads them as repeated trials.

### 3.4 `results.json` schema v2 (`eval/runner/results.py`)

Bump `RESULTS_SCHEMA_VERSION = 2` and rewrite the layout docstring.

Top level: `schema_version`, `experiment`, `run`, `started_at`, `commit`,
`commit_ref`, `config`, `environment`, `k_values`, `profiles`, `degradation`,
`summary`.

Per profile: `collection`, `prefix`, `data_root`, `source_names`, `timings`
(replaces `latency`), `rerank` (`calls`, `documents`, `tokens`, `cost`,
`cost_rub`, `failures`, `duration_ms`, `p50_ms`, `p95_ms`), `questions`.

Removed keys: `latency`, `warmup`, `reps`, `latency_k`, `cache_rerank`,
`samples_ms` (moved), `cold_samples_ms`, `cold_p50_ms`, `cold_p95_ms`,
per-question `latency_ms`, `cold_ms`.

- `validate_metric_k(k_values)` loses the `latency_k` argument; `build_results`
  loses the `latency_k` parameter.
- `summarize(profiles, k_values)` emits, per profile:
  `mean_recall@{k}`, `mean_purity@{k}`, `mean_mrr@{k}` for every k, plus
  `unanswerable_no_hit_rate` (mean over all questions × k, as today) and a
  `timings` block with `search`/`rerank`/`e2e` p50/p95/mean, plus the rerank
  cost rollup.
- `degradation(profiles, k_values)` emits `recall@{k}`, `purity@{k}`,
  `mrr@{k}` for every k (shared answerable questions) and keeps `p95_ms`
  (e2e). Remove the `latency_k` parameter.

### 3.5 `eval/runner/metrics.py`

Only `K_VALUES`/`LATENCY_K` change. `evaluate_at_k`, `percentile`, `mean`,
`delta` are untouched.

## 4. Python report framework

### 4.1 Loader (`eval/report/framework/loader.py`)

- Require `schema_version == 2`; raise a clear `ValueError` naming the file and
  version for anything else ("v1 baselines predate cache removal and are not
  comparable").
- `k_values()` reads the recorded list.
- Delete `latency_k()` and the v1 tolerance branches.
- Add accessors for the new `timings` shape if the existing helpers were
  reading `latency`.

### 4.2 Comparison (`eval/report/framework/compare.py`)

- `retrieval_metrics(results, profile, k_values)` — drop `latency_k`; emit
  `recall@k`, `purity@k`, `mrr@k` for every k, plus `search_p50_ms`,
  `search_p95_ms`, `rerank_p50_ms`, `rerank_p95_ms`, `p50_ms`/`p95_ms` (e2e) and
  `no_hit_rate`.
- Delete the `cache_rerank`/`cold_*` branch (`:120-127`).
- `question_metrics(results, profile, k_values)` returns per question a dict of
  `recall@{k}`/`purity@{k}`; the comparison table diffs every k.
- Ingest comparison is unchanged.

### 4.3 SVG (`eval/report/framework/svg.py`)

Add `horizontal_grouped_bar_chart(categories, series, ...)`:

- Categories are profiles (2 rows).
- Series are `p50`, `p95` (2 bars per row, side by side).
- Full report width; value axis horizontal with unit label (`ms`).
- Reuse the palette/number formatting of the existing vertical
  `grouped_bar_chart` (`:261-354`).

### 4.4 Render (`eval/report/framework/render.py`)

- `render_document` signature loses `latency_k`; every section builder loses
  its `latency_k` argument.
- **Summary** (`:284-317`): per profile, a metrics-as-rows table:
  rows `recall`, `purity`, `MRR` with columns `@3`, `@7`, `@20`, plus
  `no-hit rate`, `questions/answerable/unanswerable`, and e2e `p50`/`p95`.
  (Multiple profiles ⇒ one sub-table each, preserving today's profile grouping.)
- **Quality** (`:318-391`): keep the mean-recall-vs-k line chart; replace the
  single per-question table with one table per k (`hit`, `recall`, `precision`,
  `MRR`, `purity`, `hits returned`), headed by the k value.
- **Deletion**: `_latency` (`:392-457`) and `_rerank` (`:458-499`) are removed
  as separate sections.
- **Timing** (new): one section titled "Timing" with, in order:
  1. horizontal p50/p95 chart for `search`,
  2. horizontal p50/p95 chart for `rerank`,
  3. horizontal p50/p95 chart for `e2e`,
  4. aggregate table: phase × profile → mean/p50/p95/sum,
  5. reranker cost table (calls, documents, tokens, cost, cost_rub, failures),
     rendered only when `calls > 0` (keeps fusion-only runs clean).
- **Degradation** (`:500-536`): metrics × k rows/columns; keep the e2e p95
  delta.
- **Comparison** (`:668-848`): use the new `compare.retrieval_metrics` keys;
  no cold branch. Keep ingest comparison and per-question deltas, now across k.
- **Methodology** section: update to describe one query per question at
  `max(k)`, prefix truncation, no warmup/reps, and the log-based timing split.

### 4.5 Experiment scripts

- Leave existing `BASELINE_RUN` pins untouched (`eval/experiments/reranking`,
  `cloud_reranking`, `baseline`). Experiments are commit-scoped; a new pin is
  added when the v2 baseline is recorded.
- Remove any imports/uses of `latency_k` from report entry points.

## 5. Tests

### Rust (`just check`)

- `src/reranker.rs` tests: delete the cache tests (`cache_evicts_the_oldest_entry`
  and the second-query-served-from-cache assertion). Add:
  - two identical `rerank` calls ⇒ two HTTP requests (`calls == 2`);
  - success line formatting (pure function or `log_rerank` string builder);
  - failure line formatting (`status=failed`, `results=0`, `error=` tail).
- `src/store.rs`: if a store-level test harness exists for `search`, assert
  `search` timing is only measured around embed+query. Otherwise the
  `log_search` formatter unit test is sufficient.
- Hash test: known question ⇒ expected 16-hex digest, matching Python's
  `sha256(...).hexdigest()[:16]`.

### Python (`just eval-test`)

- `eval/tests/test_cli.py`: assert the new defaults, that removed flags are
  rejected, and that `--k` only accepts 3/7/20.
- `eval/tests/test_run.py`: one query per question at `max(k)`, prefix
  truncation produced the expected `by_k`, hash join attaches timings, failed
  rerank yields `status=failed` + `rerank_ms` + `null` quality degradation.
- `eval/tests/test_results.py`: rewrite `profile_result`/`build` fixtures for
  v2; assert all `mean_*@{k}` keys and all-k degradation; assert `schema_version
  == 2`; drop `latency_k` tests.
- `eval/tests/test_serve_log.py`: parse success/failure lines, `error=` tail
  with spaces, duplicate-hash anomaly, per-profile keying.
- `eval/tests/test_metrics.py`: new `K_VALUES`, no `LATENCY_K`.
- `eval/tests/test_rigrag.py`: serve command always contains
  `--log-eval-metrics` and never `--cache-rerank`.
- Report tests: rewrite `test_loader`, `test_compare`, `test_render`, `test_svg`
  for v2; add a v2 fixture; delete `eval/report/tests/fixtures/v1/` and
  `fixtures/v1_legacy/`; test that the loader refuses v1.

## 6. Docs

- `README.md`: reranker section (`:86`) — remove the cache sentence, describe
  `--log-eval-metrics`; command table (`:94`) update the `serve` row.
- `README.md` eval workflow: one query per question at max k, k grid 3/7/20,
  no warmup/reps; note that v2 replaced v1 results.
- `rig-rag.toml`, `eval/profiles/*/rig-rag.toml`, `compose.yaml`: no changes
  required (no cache flags are present).
- `Justfile`: no changes required.

## 7. Verification

Run, in order:

```sh
just check          # cargo fmt --check + clippy -D warnings + workspace tests
just eval-test      # offline Python unit tests
```

Manual smoke (no baseline recording, one question, cheap):

```sh
cargo run -- serve --log-eval-metrics
curl -s 'http://127.0.0.1:8080/api/query?q=Who%20is%20the%20author%20of%20jj%3F&k=20'
```

Assert in the serve window:

- a `search q=… k=20 candidates=… duration_ms=…` line;
- a `rerank q=… … status=success …` line with the same `q`;
- Python `sha256` of the question gives the same 16-hex `q`;
- stopping the provider / using a bad key yields `status=failed` with an
  `error=` tail and the fused fallback still returns hits.

Do **not** run `just eval-run` in this change (needs provider
spend).

## 8. Out of scope / follow-ups

- Recording the v2 baseline and repointing experiment pins.
- Any historical "before/after cache removal" comparison (v1 is unreadable by
  design).
- Changing `shared::DEFAULT_K = 7` or the HTTP API contract.
- Concurrency-safe log attribution (a single request id). Hash correlation is
  sufficient for the sequential eval; concurrent production queries would
  interleave lines but each line is still self-describing.

## 9. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Rust/Python hash mismatch | Single shared spec: UTF-8 bytes, lowercase hex, first 16 chars; unit test on both sides with the same literal question |
| Duplicate question text within a profile collapses log records | Explicit uniqueness assertion before the join |
| `candidates < max(k)` in some future config breaks truncation equivalence | Document the precondition next to `_sweep`; optionally assert `reranker.candidates >= max(k)` in eval when `[reranker]` is configured |
| Empty hit list ⇒ rerank request with zero documents | Existing behavior; note it. If the provider rejects it, the line is `status=failed` and the fused fallback (empty) is returned |
| Reader mistakes across-question p50/p95 for repeated-trial percentiles | State it in the `results.json` docstring and the Timing chart captions |
| Old v1 baseline silently compared | Loader hard-fails on `schema_version != 2` |

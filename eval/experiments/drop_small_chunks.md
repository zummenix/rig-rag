# Experiment: drop small chunks (`drop_small_chunks`)

**Hypothesis.** Skipping embedding/insertion of chunks below a token threshold
reduces ingest cost (embeddings, tokens, time, memory) with no material loss in
`recall@k`.

**Status:** **rejected (not adopted)** — measured, then discarded; see
[Decision](#decision). Directionally the hypothesis holds on the multi-project
corpus, but the cost win is small and the suite is too weak to certify no recall
regression.

## Controlled change

One line in `src/ingest.rs`: `MIN_CHUNK_TOKENS` raised from `0` (production) to
`15`, so a `DocChunk` with fewer than 15 `cl100k_base` tokens is skipped before
embedding. Chunking, the embedding model, retrieval, and the serve path are
unchanged. The change is isolated in its own commit so it can be reverted without
touching the harness (framework, runner, or this record).

| arm | `MIN_CHUNK_TOKENS` | commit | run |
| --- | ---: | --- | --- |
| baseline | 0 | `510d9915d25afdcee38c32e72cfbdcd2fc2d9126` | `eval/results/baseline/2026-10-10T15-58-51Z` |
| treatment | 15 | `25d1341b83e8a5ab0e1bc97948e5b4c8a0060512` | `eval/results/drop_small_chunks/2026-10-10T16-51-52Z` |

Both arms were ingested with `--force` (so cost is measured, not `reused`) on the
same profiles — `eval/profiles/{single,multi}-project/{rig-rag.toml,sources.json}`
(snapshotted in each run's `results.json` → `config`) — with pinned sources
`jj @ 9a1ad09b16e87d5d5e25759e5027953f8257be98`,
`podman @ e3ed4e39137fbe457dbf09c4a9d66d6e1f31e365`,
`qdrant @ 9855e50fc739de44a223a1c50faced806a868b6a`, and model
`bge-small-en-v1.5` (384 dims).

## Ingest cost

| metric | single base | single N=15 | Δ | multi base | multi N=15 | Δ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| embeddings | 793 | 788 | −5 (−0.6%) | 9,956 | 9,504 | −452 (−4.5%) |
| tokens | 151,031 | 150,972 | −59 (−0.0%) | 1,492,068 | 1,487,363 | −4,705 (−0.3%) |
| `embed` ms | 40,290 | 39,740 | −550 (−1.4%) | 443,378 | 422,093 | −21,285 (−4.8%) |
| `insert` ms | 586 | 575 | −11 | 8,414 | 8,129 | −285 |
| `total` ms | 44,600 | 43,937 | −663 (−1.5%) | 469,987 | 447,408 | −22,579 (−4.8%) |
| peak RSS (GiB) | 9.03 | 8.20 | −0.84 | 12.48 | 9.40 | −3.08 |

Multi by source (chunks): `jj` 793 → 788 (−5), `podman` 2,509 → 2,121
(−388, −15.5%), `qdrant` 6,654 → 6,595 (−59). Almost all of the saving is
podman's tiny fragments.

## Retrieval

| metric | single base | single N=15 | multi base | multi N=15 |
| --- | ---: | ---: | ---: | ---: |
| mean recall@1 | 1/3 | 1/3 | 1/6 | 1/6 |
| mean recall@7 | 1/3 | 1/3 | 1/6 | 1/6 |
| mean recall@10 | 1/3 | 1/3 | 1/3 | 1/3 |
| mean recall@20 | 1/3 | 1/3 | 1/3 | 1/3 |
| mean purity@7 | 1.000 | 1.000 | 1.000 | 0.976 |
| mean MRR@7 | 1/3 | 1/3 | 1/6 | 1/6 |
| p50 ms | 7.18 | 7.22 | 7.58 | 7.54 |
| p95 ms | 8.17 | 7.81 | 9.27 | 8.83 |
| unanswerable no-hit rate | — | — | 1.000 | 1.000 |

No per-question `recall@7` changed. The only movement is `cross-project-config`
purity@7 `1.000 → 0.857` (6/7 top hits from its gold sources): a non-gold-source
hit entered its top-7 while its `recall` stayed `0.0` in both arms. The
single→multi `recall@k` degradation delta is `0.0` in both arms.

## Conclusion

`N = 15` removes ~4.5% of multi-project embeddings and ~4.8% of its embed/total
time (nearly all from `podman`), with no `recall@k` change anywhere in the suite;
single-project shrinks 0.6%, within run-to-run noise. Directionally the
hypothesis holds, but the win is modest at a conservative threshold. Peak RSS
also fell in both profiles, though RSS is host-dependent and not trustworthy on
its own.

## Decision

**Not adopted.** Production reverts to embedding every chunk: the production
change (the `MIN_CHUNK_TOKENS` seam and the `min_chunk_tokens` report field) is
reverted in a follow-up commit, leaving this record, the framework, and the
measured runs intact. Rationale:

- **The cost win is small and corpus-skewed.** ~4.5% of multi-project embeddings
  (almost entirely `podman` fragments) and ~4.8% of its embed/total time; the
  single-project corpus changes by 0.6%, within noise. Not enough on its own to
  justify a permanent ingest knob.
- **The quality signal is too weak to certify safety.** With 3 single / 6 multi
  answerable questions the suite bounds only large recall regressions. The one
  metric that moved — `cross-project-config` purity@7 `1.000 → 0.857` — moved the
  *wrong* way (a near-tied qdrant hit entered rank 7) even though recall held.
- **Revisit with a stronger suite.** Re-run this arm once `eval/questions.toml`
  has enough questions to detect small recall changes; the machinery is cheap to
  re-apply.

The measured artifacts (`eval/results/baseline/2026-10-10T15-58-51Z`,
`eval/results/drop_small_chunks/2026-10-10T16-51-52Z`) and the treatment commit
`25d1341b83e8a5ab0e1bc97948e5b4c8a0060512` stay in history so the numbers above
remain reproducible.

## Caveats

- **Suite power.** The set has 3 answerable single-project and 6 multi-project
  questions; it can bound a large recall regression but not a small one. "No
  regression" here means *none detectable by this suite*.
- **Tokenizer.** The threshold uses `tiktoken:cl100k_base`, an approximation of
  BGE's WordPiece vocabulary: token counts, and therefore which chunks are
  dropped, are approximate.
- **Memory/latency noise.** Peak RSS varies run-to-run; p50/p95 differ by well
  under a millisecond. Do not read those deltas as effects.

## Reproduction

```sh
just eval-run --experiment baseline --force-ingest          # MIN_CHUNK_TOKENS = 0
just eval-run --experiment drop_small_chunks --force-ingest # MIN_CHUNK_TOKENS = 15
just eval-report drop_small_chunks                          # render from committed JSON
```

## Embedded summary (JSON)

```json
{
  "experiment": "drop_small_chunks",
  "min_chunk_tokens": 15,
  "baseline": {"commit": "510d9915d25afdcee38c32e72cfbdcd2fc2d9126", "run": "2026-10-10T15-58-51Z"},
  "treatment": {"commit": "25d1341b83e8a5ab0e1bc97948e5b4c8a0060512", "run": "2026-10-10T16-51-52Z"},
  "ingest_delta": {
    "single-project": {"embeddings": -5, "tokens": -59, "embed_ms": -550, "total_ms": -663},
    "multi-project": {"embeddings": -452, "tokens": -4705, "embed_ms": -21285, "total_ms": -22579}
  },
  "retrieval_delta": {
    "single-project": {"recall@7": 0.0, "purity@7": 0.0, "p95_ms": -0.36},
    "multi-project": {"recall@7": 0.0, "purity@7": -0.024, "p95_ms": -0.44}
  }
}
```

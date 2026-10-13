# Dependency cleanup

Currently `rig` crate requires bringing `fastembed` through `fastembed` feature.
The `rig` uses very old version of `fastembed` which stops us using new features
of newer version of the crate. Additionally, `rig` requires using `rig-qdrant`
crate to work with Qdrant vector database. I think we can pull and use
`fastembed` and `qdrant-client` crates directly. This will allow us to not
depend on rig's wrappers and use all new features.

## Work plan

- [x] Spike on features `fastembed` and `qdrant-client` crates provide and how
      they might work together
- [x] Use knowledge after the spike to integrate `fastembed` and `qdrant-client`
      crates directly into the codebae instead of rig
- [x] Run eval and check that we don't hit regression in query
- [x] Remove rig's `fastembed` feature
- [ ] Remove `rig-qdrant` crate
- [ ] Update `fastembed` to the latest version
- [ ] Run eval again, remove this plan

## Status

The spike is integrated. `rig`'s embedding and Qdrant wrappers are replaced by
direct `fastembed`/`qdrant-client` calls in `src/embedding.rs`, `src/store.rs`,
`src/retrieval.rs`, `src/ingest.rs` and `src/serve/prod.rs`; the temporary `v2`
modules and the `rig::Embed` impl are gone. Retrieval stays dense-only (single
unnamed cosine vector) so it matches the existing collections and the baseline.

The spike's hybrid search was removed for now (recoverable from commit
`9ecb299`); re-add and eval test it separately after the dependency cleanup.

The first eval run is done. No changes to retrieval quality. The latency grew up
a little: p95 is ~12ms vs ~10ms; Timing embed grew up: ~600s vs ~440s. Results
weren't saved (will be saved at the last step)


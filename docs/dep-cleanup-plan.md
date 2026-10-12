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
- [ ] Use knowledge after the spike to integrate `fastembed` and `qdrant-client`
      crates directly into the codebae instead of rig
- [ ] Run eval and check that we don't hit regression in query
- [ ] Remove rig's `fastembed` feature
- [ ] Remove `rig-qdrant` crate
- [ ] Run eval again, remove this plan

## Status

The spike is done. In `src/embedding.rs` and `src/store.rs` were added temporary
`v2` modules just to make sure that we have all functionality we need and the
code base compiles. The code is very straight, needs refactor during integration
stage.

Note: that the code contains partially implemented hybrid search (this is one
of the reasons we should use crates directly because rig's wrappers do not allow
such agility). Refactoring is advised: remove hybrid search for now or disable
it through a bool flag or enum. We will eval test the hybrid search in the
future after dependency cleanup.

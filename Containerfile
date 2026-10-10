# syntax=docker/dockerfile:1

# --- Native build stage ------------------------------------------------------
FROM rust:1.98-bookworm AS builder
WORKDIR /app

# Native build deps: build-essential for C crates (aws-lc-sys, ring),
# libssl-dev/pkg-config for native-tls.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Compile dependencies in their own layer so source edits do not rebuild them.
# The workspace has three members (`rig-rag`, `shared`, `site`); only `rig-rag`
# and its `shared` dependency are built here, but every member manifest must be
# present for cargo to resolve the workspace.
COPY Cargo.toml Cargo.lock ./
COPY shared/Cargo.toml ./shared/Cargo.toml
COPY site/Cargo.toml ./site/Cargo.toml
RUN mkdir -p src shared/src site/src \
    && echo 'fn main() {}' > src/main.rs \
    && : > src/lib.rs \
    && : > shared/src/lib.rs \
    && echo 'fn main() {}' > site/src/main.rs \
    && cargo build --release --locked -p rig-rag \
    && rm -rf src shared/src

COPY src ./src
COPY shared ./shared

# COPY preserves source mtimes, which can predate the stub build's artifacts, so
# cargo would treat these crates as fresh and silently keep the stub binary. Bump
# the mtimes to force a real rebuild.
RUN find src shared -type f -exec touch {} +

# `rig-rag model` loads the embedding model, baking its weights into the image.
ENV FASTEMBED_CACHE_DIR=/app/.fastembed_cache
RUN cargo build --release --locked -p rig-rag \
    && ./target/release/rig-rag model \
    && ls -la /app/.fastembed_cache

# The ort build script downloads ONNX Runtime into the build cache. Collect any
# shared library for the runtime stage; the prebuilt static library makes this a
# no-op today, but it keeps the image correct should ort ship a .so.
RUN mkdir -p /app/ort-libs \
    && find / -name 'libonnxruntime.so*' -exec cp -av {} /app/ort-libs/ \; 2>/dev/null || true

# --- Web UI (WASM) build stage -----------------------------------------------
FROM rust:1.98-bookworm AS site-builder
WORKDIR /app

# trunk orchestrates the wasm build and downloads the matching wasm-bindgen-cli.
RUN rustup target add wasm32-unknown-unknown \
    && cargo install trunk --locked

# The whole workspace is needed for cargo to resolve `site`; only its members are
# copied (the native sources are irrelevant here).
COPY Cargo.toml Cargo.lock ./
COPY shared ./shared
COPY site ./site
RUN cd site && trunk build --release \
    && ls -la /app/site/dist

# --- Runtime stage -----------------------------------------------------------
FROM debian:bookworm-slim AS runtime

# libssl3: native-tls; libgomp1: onnxruntime; ca-certificates: TLS roots for
# the OpenRouter call. Source fetching (and its `git` dependency) happens on the
# host, so the runtime image does not need git.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libssl3 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/ort-libs/ /usr/local/lib/

WORKDIR /app
COPY --from=builder /app/target/release/rig-rag /usr/local/bin/rig-rag
COPY --from=builder /app/.fastembed_cache /app/.fastembed_cache
COPY --from=site-builder /app/site/dist /app/site/dist

ENV FASTEMBED_CACHE_DIR=/app/.fastembed_cache

ENTRYPOINT ["rig-rag"]
CMD ["--help"]

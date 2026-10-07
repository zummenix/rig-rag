# syntax=docker/dockerfile:1

# --- Build stage -------------------------------------------------------------
FROM rust:1.98-bookworm AS builder
WORKDIR /app

# Native build deps: build-essential for C crates (aws-lc-sys, ring),
# libssl-dev/pkg-config for native-tls.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Compile dependencies in their own layer so source edits do not rebuild them.
COPY Cargo.toml Cargo.lock ./
RUN mkdir src \
    && echo 'fn main() {}' > src/main.rs \
    && : > src/lib.rs \
    && cargo build --release --locked \
    && rm -rf src

COPY src ./src

# COPY preserves source mtimes, which can predate the stub build's artifacts, so
# cargo would treat this crate as fresh and silently keep the stub binary. Bump
# the mtimes to force a real rebuild.
RUN find src -type f -exec touch {} +

# `rig-rag model` loads the embedding model, baking its weights into the image.
ENV FASTEMBED_CACHE_DIR=/app/.fastembed_cache
RUN cargo build --release --locked \
    && ./target/release/rig-rag model \
    && ls -la /app/.fastembed_cache

# The ort build script downloads ONNX Runtime into the build cache. Collect any
# shared library for the runtime stage; the prebuilt static library makes this a
# no-op today, but it keeps the image correct should ort ship a .so.
RUN mkdir -p /app/ort-libs \
    && find / -name 'libonnxruntime.so*' -exec cp -av {} /app/ort-libs/ \; 2>/dev/null || true

# --- Runtime stage -----------------------------------------------------------
FROM debian:bookworm-slim AS runtime

# git: source fetching (fetch.rs); libssl3: native-tls; libgomp1: onnxruntime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates libssl3 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/ort-libs/ /usr/local/lib/

WORKDIR /app
COPY --from=builder /app/target/release/rig-rag /usr/local/bin/rig-rag
COPY --from=builder /app/.fastembed_cache /app/.fastembed_cache

ENV FASTEMBED_CACHE_DIR=/app/.fastembed_cache

ENTRYPOINT ["rig-rag"]
CMD ["--help"]

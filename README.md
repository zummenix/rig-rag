# rig-rag

Retrieval-augmented generation over documentation fetched from configured
sources. Documents are chunked, embedded locally with fastembed, and stored in
Qdrant; `serve` exposes retrieval and streaming RAG chat over HTTP, with an
optional WASM web UI.

## Requirements

- [Qdrant](https://qdrant.tech/) reachable at `http://localhost:6334` (or set
  `[qdrant].url` / `QDRANT_URL`)
- `git` on `PATH` (used to fetch sources)
- For `serve`: `OPENROUTER_API_KEY` and `OPENROUTER_MODEL_NAME`
- For building the web UI (`site/`): the `wasm32-unknown-unknown` target and
  [`trunk`](https://trunkrs.dev/) (`rustup target add wasm32-unknown-unknown` +
  `cargo install trunk`)

When using podman on macOS, give the machine enough memory — the default 2 GiB
is OOM-killed during the release build:

```sh
podman machine set --memory 24576 --cpus 8
```

## Configuration

`sources.json` — the documents to ingest. Each entry:

```json
{
  "name": "jj",
  "type": "git",
  "url": "https://github.com/jj-vcs/jj",
  "ref": "main",
  "path": "docs"
}
```

`name` must be unique and contain only `[A-Za-z0-9_-]`; it becomes the
directory under `data/`. `ref` (default `main`) and `path` (default `.`, meaning
the whole repo) are optional. The corpus is exactly the sources listed here, so
removing a source removes its documents from the next ingest.

`rig-rag.toml`:

```toml
[qdrant]
url = "http://localhost:6334"

[embedding]
model = "bge-small-en-v1.5"

[collection]
# Real collection name; set by `rig-rag promote <collection>`.
active = ""

# Optional; every field falls back to a default and is overridden by the
# matching `serve` flag.
[server]
bind = "127.0.0.1:8080"
site_dir = "./site/dist"
keep_alive_secs = 15
```

`[collection].active` names the collection that `serve` reads from. It is empty
until you promote something; until then `serve` exits with a clear error.

## Commands

| Command | Description |
| --- | --- |
| `rig-rag ingest [--force] [--report <path>]` | Fetch sources, embed the corpus, insert into a new collection (optionally write a JSON report) |
| `rig-rag serve` | Serve the retrieval/chat API + built UI over HTTP |
| `rig-rag promote <collection>` | Set the active collection in `rig-rag.toml` |
| `rig-rag prune [--yes]` | Delete collections other than the active one (dry run without `--yes`) |
| `rig-rag model` | Load the embedding model and print its id and dimensions |

## Local workflow

```sh
# 1. Build a collection from the current sources.
cargo run -- ingest
# -> prints the new collection name, e.g. docs-bge-small-en-v1-5-1a2b3c4d5e6f

# 2. Make it live (rewrites [collection].active).
cargo run -- promote docs-bge-small-en-v1-5-1a2b3c4d5e6f
```

### Dev servers

During development two long-running processes are useful. The API watcher
requires [`cargo-watch`](https://github.com/watchexec/cargo-watch) (`cargo
install cargo-watch`):

```sh
just dev-site   # trunk on :3000, hot-reloads the UI, proxies /api/* to :8080
just dev-api    # cargo-watch rebuilds + restarts the axum server on :8080
```

Run them in separate terminals. `dev-site` only watches the UI; `dev-api`
watches server changes, so frontend edits don't restart the backend. `dev-api`
needs Qdrant, an active collection, and the `OPENROUTER_*` vars — `serve`
refuses to boot without them. Recompiling re-binds `:8080`, so expect a brief
blip in the UI during rebuilds.

## Web UI (`rig-rag serve`)

`serve` loads the config and **refuses to start** unless Qdrant, an active
collection, and `OPENROUTER_*` are all usable — the retrieval tab needs no LLM,
but the server boots as a whole. It binds `127.0.0.1:8080` by default; `--bind`
and `--site-dir` override the optional `[server]` section. When the site
directory exists, the built UI is served at `/` alongside the API.

Build the UI once, then start the server:

```sh
cd site && trunk build --release && cd ..
cargo run -- serve
# -> rig-rag serving on http://127.0.0.1:8080 (site: ./site/dist)
```

API: `GET /api/health`, `POST /api/query` (`{"question", "k"?}`), and
`POST /api/chat` (SSE). Chat is stateless per turn — the client sends the whole
transcript as `{"history": [...], "prompt": "..."}` and the server retrieves on
the latest user message.

## Web UI tests

The Leptos UI in `site/` has a Playwright end-to-end suite (`site/tests`) that
drives the real router and the built WASM bundle against the mock backends
(`--features mock`) — no Qdrant or OpenRouter needed. One-time setup, then run:

```sh
cd site/tests
npm install
npx playwright install chromium   # downloads the browser
npx playwright test
```

`global-setup.ts` runs `cargo build --features mock --bin mock_server` and
`trunk build` (for `site/dist`) before launching the servers, so make sure
`trunk` and the `wasm32-unknown-unknown` target are installed. Set
`RIG_RAG_SKIP_BUILD=1` to reuse existing builds.

## Container workflow (podman)

The compose stack runs Qdrant and the `server` image; `ingest`/`promote` run on
the host against the compose-published Qdrant (`:6334`):

```sh
podman compose up -d qdrant
cargo run -- ingest
cargo run -- promote docs-bge-small-en-v1-5-1a2b3c4d5e6f
podman compose up -d server   # UI at http://localhost:8080
```

The `server` service runs `serve` on port `8080`; it refuses to boot until an
active collection and OpenRouter creds are available, so run `ingest`/`promote`
first.

The `Containerfile` builds the WASM UI in a separate `site-builder` stage
(`wasm32-unknown-unknown` + `trunk`) and copies `site/dist` into the runtime
image at `/app/site/dist`. The container has no `trunk`; rebuild the image to
pick up UI changes.

The `server` service mounts `rig-rag.toml` (read-write), `sources.json`
(read-only), and `data/`, and talks to `qdrant` over the compose network
(`QDRANT_URL=http://qdrant:6334`). Both `data/` and Qdrant's storage
(`./qdrant_storage`, bind-mounted to `/qdrant/storage`) live on the host and
persist across runs.

The image bakes both ONNX Runtime and the embedding weights, so `serve` needs no
network beyond OpenRouter.

### OpenRouter credentials

`serve` needs `OPENROUTER_API_KEY` and `OPENROUTER_MODEL_NAME`. `compose.yaml`
interpolates both from the environment, so either export them before running
compose or drop them in a `.env` file next to `compose.yaml` (gitignored):

```sh
# .env
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL_NAME=openai/gpt-5-mini
```

`cargo run -- serve` on the host reads the same variables.

## How it works

- **Hashing.** Each source directory is hashed (sorted relative paths + bytes);
  the per-source hashes are combined into one. The combined hash, together with
  the model slug, names the target collection: `docs-<model>-<hash12>`. If that
  collection already exists, `ingest` is a no-op.
- **Blue/green promotion.** Ingest only ever creates new collections; it never
  changes which one is active. `promote` rewrites `[collection].active`, so a
  bad ingest stays invisible until you decide to switch, and the previous
  collection remains for rollback until `prune`.
- **Model identity.** The model slug is part of every collection name, so
  `serve` can refuse a collection built with a different model than the
  configured one. Qdrant itself rejects dimension mismatches.

## Notes

- `ingest` re-embeds every source in one process and peaks around 20-25 GiB of
  RAM; run it on a host with enough memory.
- Changing `[embedding].model` requires a re-ingest and re-promote; the baked
  image only contains the default model's weights.
- A changed corpus builds a whole new collection: every source is re-embedded.
- `rig-rag ingest` prints peak process RSS and, in a container, the cgroup
  memory peak.
- `ingest --report <path>` additionally writes a schema-versioned JSON report
  (per-phase timings, memory, per-source/total counts, and a `cl100k_base`
  chunk-token distribution). Nothing extra — in particular no tokenization or
  timing — runs when the flag is absent.

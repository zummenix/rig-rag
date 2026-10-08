# rig-rag

Retrieval-augmented generation over documentation fetched from configured
sources. Documents are chunked, embedded locally with fastembed, and stored in
Qdrant; `query` and `chat` retrieve from the active collection. `serve` exposes
the same retrieval and chat over HTTP, with streaming chat responses and an
optional WASM web UI.

## Requirements

- [Qdrant](https://qdrant.tech/) reachable at `http://localhost:6334` (or set
  `[qdrant].url` / `QDRANT_URL`)
- `git` on `PATH` (used to fetch sources)
- For `chat` and `serve`: `OPENROUTER_API_KEY` and `OPENROUTER_MODEL_NAME`
- For building the web UI (`site/`): the `wasm32-unknown-unknown` target and
  [`trunk`](https://trunkrs.dev/) (`rustup target add wasm32-unknown-unknown` +
  `cargo install trunk`)

When using podman on macOS, give the machine enough memory. The default 2 GiB is
OOM-killed during the release build, and `ingest` itself needs ~20-25 GiB:

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

`[collection].active` names the collection that `query`/`chat` read from. It is
empty until you promote something; until then those commands exit with a clear
error.

## Commands

| Command | Description |
| --- | --- |
| `rig-rag ingest [--force]` | Fetch sources, embed the corpus, insert into a new collection |
| `rig-rag query -q <question>` | Print the closest chunks and their scores |
| `rig-rag chat` | Interactive RAG chatbot over the active collection |
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

# 3. Ask questions.
cargo run -- query -q "how do I squash commits?"
cargo run -- chat
```

### Dev servers

During development two long-running processes are useful:

```sh
just dev-site   # trunk on :3000, hot-reloads the UI, proxies /api/* to :8080
just dev-api    # cargo-watch rebuilds + restarts the axum server on :8080
```

Run them in separate terminals. `dev-site` only watches the UI; `dev-api`
watches server changes, so frontend edits don't restart the
backend. `dev-api` needs Qdrant and an active collection, and (for chat) the
`OPENROUTER_*` vars — `serve` refuses to boot without them. Recompiling
re-binds `:8080`, so expect a brief blip in the UI during rebuilds.

## Web UI (`rig-rag serve`)

`serve` loads the same config as `query`/`chat` and **refuses to start** unless
Qdrant, an active collection, and `OPENROUTER_*` are all usable — the retrieval
tab needs no LLM, but the server boots as a whole. It binds `127.0.0.1:8080` by
default; `--bind` and `--site-dir` override the optional `[server]` section.
When the site directory exists, the built UI is served at `/` alongside the API.

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

```sh
podman compose up -d qdrant
podman compose run --rm app ingest
podman compose run --rm app promote docs-bge-small-en-v1-5-1a2b3c4d5e6f
podman compose run --rm -it app chat
```

To serve the API and UI, start the `server` service (same image, `serve` as its
command, port `8080`); it refuses to boot until an active collection and
OpenRouter creds are available, so run `ingest`/`promote` first:

```sh
podman compose up -d qdrant server   # UI at http://localhost:8080
```

The `Containerfile` builds the WASM UI in a separate `site-builder` stage
(`wasm32-unknown-unknown` + `trunk`) and copies `site/dist` into the runtime
image at `/app/site/dist`. The container has no `trunk`; rebuild the image to
pick up UI changes.

The `app` container mounts `sources.json` (read-only), `rig-rag.toml`
(read-write, so `promote` can rewrite it), and `data/`, and talks to `qdrant`
over the compose network (`QDRANT_URL=http://qdrant:6334`). The `server` service
mounts the same files. Both `data/` and Qdrant's storage (`./qdrant_storage`,
bind-mounted to `/qdrant/storage`) live on the host and persist across runs.

The image bakes both ONNX Runtime and the embedding weights, so ingest needs no
network beyond the source clones.

### OpenRouter credentials

`chat` and `serve` need `OPENROUTER_API_KEY` and `OPENROUTER_MODEL_NAME`.
`compose.yaml` interpolates both from the environment, so either export them
before running compose or drop them in a `.env` file next to `compose.yaml`
(gitignored):

```sh
# .env
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL_NAME=openai/gpt-5-mini
```

The same variables are read by `cargo run -- chat` on the host.

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
  `query`/`chat` can refuse a collection built with a different model than the
  configured one. Qdrant itself rejects dimension mismatches.

## Notes

- Changing `[embedding].model` requires a re-ingest and re-promote; the baked
  image only contains the default model's weights.
- A changed corpus builds a whole new collection: every source is re-embedded.
- `rig-rag ingest` prints peak process RSS and, in a container, the cgroup
  memory peak.

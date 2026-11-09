# rig-rag

Retrieval-augmented generation over documentation fetched from configured
sources. Documents are chunked, embedded locally with fastembed, and stored in
Qdrant; `serve` exposes retrieval and streaming RAG chat over HTTP, with an
optional WASM web UI.

## Requirements

- [Qdrant](https://qdrant.tech/) reachable at `http://localhost:6334` (or set
  `[qdrant].url` / `QDRANT_URL`)
- `git` on `PATH` (used to fetch sources)
- For `serve` chat: `OPENAI_API_KEY` or `OPENROUTER_API_KEY` when `[llm].provider`
  is `openai` or `openrouter` respectively; `OLLAMA_API_KEY` and `LLM_API_KEY`
  are optional for `ollama` and `custom`
- For `serve` with a `[reranker]` section: `RERANKER_API_KEY` (see below)
- For building the web UI (`site/`): the `wasm32-unknown-unknown` target and
  [`trunk`](https://trunkrs.dev/) (`rustup target add wasm32-unknown-unknown` +
  `cargo install trunk`)

The chat LLM is optional: omit the `[llm]` section from `rig-rag.toml` and
retrieval still works, but `serve` boots with chat disabled and `POST /api/chat`
answers with a clear `503`.

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

### Chat LLM (`[llm]`)

Optional. Without it, `serve` boots with retrieval only and chat is disabled.

```toml
# Tracked config in this repository (the author's locally available model).
[llm]
provider = "ollama"
model = "mimo-v2.6:9b-mxfp8"
```

- `provider` is required when the section exists and must be one of
  `openai`, `openrouter`, `ollama`, `custom`. Anything else is rejected at
  config load.
- `model` is required, non-empty, and used verbatim as the exact
  provider-native id — no auto-prefixing, no shape validation.
- `base_url` is optional for the built-in providers. When omitted it defaults
  to `https://openrouter.ai/api/v1` (`openrouter`),
  `https://api.openai.com/v1` (`openai`), or
  `http://localhost:11434/v1` (`ollama`); a configured value overrides that
  default (for a proxy or a remote daemon). It is required for `custom`.
  Surrounding whitespace and trailing slashes are trimmed.
- There is no `api_key` field and no tuning fields (temperature, max tokens,
  timeout); the provider's own defaults apply.

Examples:

```toml
# Any Ollama model installed on the host.
[llm]
provider = "ollama"
model = "llama3.2:7b"
```

```toml
# OpenRouter; base_url defaults to https://openrouter.ai/api/v1.
[llm]
provider = "openrouter"
model = "openai/gpt-5-mini"
```

```toml
# OpenAI; base_url defaults to https://api.openai.com/v1.
[llm]
provider = "openai"
model = "gpt-5-mini"
```

```toml
# Any other OpenAI-compatible gateway; base_url is mandatory.
[llm]
provider = "custom"
base_url = "https://api.groq.com/openai/v1"
model = "llama-3.3-70b-versatile"
```

Credentials are read from the environment, never from this tracked file:

| Provider | Variable | Required | Notes |
| --- | --- | --- | --- |
| `openrouter` | `OPENROUTER_API_KEY` | yes | Startup error if unset or empty |
| `openai` | `OPENAI_API_KEY` | yes | Startup error if unset or empty |
| `ollama` | `OLLAMA_API_KEY` | no | Sent as Bearer only when non-empty; stock Ollama ignores it |
| `custom` | `LLM_API_KEY` | no | Sent as Bearer only when non-empty |

Values are trimmed; an empty string is treated as unset. There is no fallback
from one provider's variable to another, and `LLM_API_KEY` is not a generic
fallback for the cloud providers. There is no `LLM_BASE_URL` or other endpoint
override — the endpoint lives in `rig-rag.toml`.

Changing the chat LLM or the reranker never requires a re-ingest: collection
names encode the embedding model slug and corpus hash only. Changing
`[embedding].model` still requires a re-ingest and re-promote.

### Ollama context

Ollama's OpenAI-compatible endpoint cannot set `num_ctx` or `keep_alive` per
request (only the native `/api/chat` can). The RAG prompt can reach roughly
seven 512-token chunks plus ten history messages, which can exceed Ollama's
default context window and truncate the start of the prompt. Choose a model
with a large enough context window, or raise the daemon's context:

```sh
OLLAMA_CONTEXT_LENGTH=16384 ollama serve
```

or in the Modelfile:

```
PARAMETER num_ctx 16384
```

### Reranker (`[reranker]`)

Optional cloud reranking (omit the section for retrieval without reranking):

```toml
[reranker]
# API root including the version segment; the app posts to {base_url}/rerank.
base_url = "https://api.jina.ai/v1"
model = "jina-reranker-v2-base-multilingual"
# Retrieved candidates scored before cutting to k (default 60).
candidates = 60
# Per-request timeout; cloud rerankers can take seconds (default 10000).
timeout_ms = 10000
```

The authorization token is read from `RERANKER_API_KEY` and sent as
`Authorization: Bearer`; it is never stored in this tracked file. When the
section is present, `serve` fetches `max(k, candidates)` hits in the requested
mode, rescores them, and returns the best `k`. A reranker error or timeout falls
back to the retrieval order with a warning. `serve --log-eval-metrics` emits structured
per-query `search`/`rerank` lines to stderr (correlated by a hash of the
question) for the eval runner; it never changes retrieval behavior.

`rig-rag model` prints the configured reranker alongside the embedding model id
and dimensions.

## Commands

| Command | Description |
| --- | --- |
| `rig-rag ingest [--force] [--report <path>]` | Fetch sources, embed the corpus, insert into a new collection (optionally write a JSON report) |
| `rig-rag serve [--log-eval-metrics]` | Serve the retrieval/chat API + built UI over HTTP (optionally emit per-query eval metrics) |
| `rig-rag promote <collection>` | Set the active collection in `rig-rag.toml` |
| `rig-rag prune [--yes]` | Delete collections other than the active one (dry run without `--yes`) |
| `rig-rag model` | Load the embedding model and print its id, dimensions, and configured reranker |

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
needs Qdrant and an active collection; it no longer needs LLM variables —
without `[llm]` the server boots normally and `POST /api/chat` returns `503`
with a clear message instead of failing the process. Recompiling re-binds
`:8080`, so expect a brief blip in the UI during rebuilds.

## Web UI (`rig-rag serve`)

`serve` requires Qdrant and an active collection; it binds
`127.0.0.1:8080` by default; `--bind` and `--site-dir` override the optional
`[server]` section. When the site directory exists, the built UI is served at
`/` alongside the API. The retrieval tab needs no LLM, so without `[llm]`
retrieval works and `POST /api/chat` returns `503` with a clear message.

Build the UI once, then start the server:

```sh
cd site && trunk build --release && cd ..
cargo run -- serve
# -> rig-rag serving on http://127.0.0.1:8080 (site: ./site/dist)
```

API: `GET /api/health`, `POST /api/query` (`{"question", "k"?, "mode"?}`), and
`POST /api/chat` (SSE). Chat is stateless per turn — the client sends the whole
transcript as `{"history": [...], "prompt": "..."}` and the server retrieves on
the latest user message (always with hybrid search).

For `POST /api/query`, `mode` picks which vectors to search and defaults to
`hybrid`: `"dense"` (embedding similarity only), `"sparse"` (BM25 keyword search
only, skipping the local embedding), or `"hybrid"` (both, fused with RRF). An
unknown value is rejected with `422`.

The `score` on each returned hit is **per-mode**: an RRF score for `hybrid`, a
cosine similarity for `dense`, and a BM25 score for `sparse`. Without a
reranker, scores are only meaningful as a ranking within one mode and one
response. When a reranker succeeds, every returned hit carries that reranker's
relevance score instead, so scores are comparable across modes for the same
query (but not across runs or reranker models); a failed rerank falls back to
retrieval order and raw scores.

The eval harness sweeps `hybrid`, `dense`, and `sparse` in one ingest/serve pass
by default — each question is queried once per mode — and records all three in
`results.json`; `--modes` narrows it. See [`eval/README.md`](eval/README.md).

## Web UI tests

The Leptos UI in `site/` has a Playwright end-to-end suite (`site/tests`) that
drives the real router and the built WASM bundle against the mock backends
(`--features mock`) — no Qdrant or LLM needed. One-time setup, then run:

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

## Host-run workflow

Qdrant runs in compose; `ingest`, `promote`, and `serve` run on the host against
the compose-published Qdrant (`:6334`):

```sh
podman compose up -d qdrant
cargo run -- ingest
cargo run -- promote docs-bge-small-en-v1-5-1a2b3c4d5e6f
cargo run -- serve   # UI at http://localhost:8080
```

`compose.yaml` defines only the Qdrant service, with `./qdrant_storage`
bind-mounted so its data is visible on the host. No application image is built,
and CI never built one. `ingest` reads `sources.json` and writes `data/`; `serve`
reads `rig-rag.toml` and connects to Qdrant at `[qdrant].url` (`QDRANT_URL`
overrides it). Chat models run natively on the host (see the Ollama context
section above), not in a container.

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
- Changing `[embedding].model` requires a re-ingest and re-promote; the
  embedding weights live in `.fastembed_cache/` on the host and are warmed by
  `rig-rag model` or the first `ingest`/`serve`.
- A changed corpus builds a whole new collection: every source is re-embedded.
- `rig-rag ingest` prints peak process RSS and, on Linux, the cgroup memory
  peak when running under one.
- `ingest --report <path>` additionally writes a schema-versioned JSON report
  (per-phase timings, memory, per-source/total counts, and a `cl100k_base`
  chunk-token distribution). Nothing extra — in particular no tokenization or
  timing — runs when the flag is absent.

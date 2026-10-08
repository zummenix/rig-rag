# rig-rag Web UI — Implementation Plan

Status: **Phase 0 done; Phases 1–5 not yet implemented.** Written 2026-10-08.
Next session: start at "Phase 1". This document is the source of truth for
decisions; update it when a phase changes a decision.

Phase 0 landed: `shared` crate (wire types + `k` bounds), `Retriever` /
`Completer` traits in `src/serve/state.rs`, `src/retrieval.rs` extracted from
`query.rs`, and prompt-assembly / history-cap helpers in `src/serve/chat.rs`.
One decision changed: `DocHit.score` is `f64`, not `f32` (rig returns `f64`;
narrowing would make `query` CLI output non-byte-identical).

## Goal

Add a web UI (served by a new `rig-rag serve` subcommand) that exposes the two
existing capabilities — retrieval (`query`) and RAG chat (`chat`) — over HTTP,
with streaming chat responses. The existing `query`, `chat`, `ingest`,
`promote`, `prune`, and `model` subcommands keep working unchanged in behavior
and UX.

## Agreed decisions

| # | Branch | Decision |
|---|--------|----------|
| 1 | Retrieval | **Server retrieves explicitly** with `top_n`. A real `docs` event is emitted with the exact hits the model will see. `dynamic_context` is **not** used on the web path. |
| 2 | History | **Client owns the transcript.** The server keeps no conversation state. Each turn is a pure function of `(history, prompt)`. |
| 3 | Protocol | REST for `POST /api/query` + `GET /api/health`; **SSE** for chat: `POST /api/chat` → `200 text/event-stream`. |
| 4 | SSE shape | One request per turn, stateless. **No `EventSource`** (it is GET-only and cannot carry a body); the client reads the response body stream via `fetch`/`reqwest` and decodes SSE frames. |
| 5 | Mock seam | Trait DI. Our own `Retriever` and `Completer` traits; production impls wrap Qdrant and rig; the router is built from injectable state. |
| 6 | Frontend | **Leptos (CSR) + trunk**, compiled to WASM. |
| 7 | Serving | axum serves trunk's `dist/` with tower-http `ServeDir` from `--site-dir` (default `./site/dist`). One process. |
| 8 | Agent scope | v1 = retrieval + streamed text (+ `thinking` reasoning deltas when the model emits them). **No tools.** `tool_call`/`tool_result` variants are reserved in the schema but never emitted. |
| 9 | Reuse | Small **behavior-preserving** extraction of retrieval into a shared function; CLI output unchanged. |
| 10 | Entrypoint | `rig-rag serve` subcommand with `--bind` / `--site-dir` flags and an **optional** `[server]` config section (flags override; section absent = defaults). |
| 11 | Turn semantics | Retrieve on the **latest user message**; forward at most the **last ~10 history messages** to the LLM (client still holds the full transcript). |
| 12 | Compose | New `server` service (same image) with `command: ["serve", ...]`; `app` stays for one-shot tasks. Containerfile gains a WASM build stage producing `/app/site/dist`. |
| 13 | Test env | Local-first. Inline `#[cfg(test)]` units + `tests/api_e2e.rs` (in-process real router on port 0 with mocks). Playwright under `site/` launches the feature-gated `mock-server`. No CI yet; prerequisites documented. |
| 14 | Startup | **Require everything.** `serve` refuses to start unless Qdrant + an active collection + OpenRouter creds/model are all available. (Consequence: the `query` tab needs no LLM but the server still will not boot without `OPENROUTER_*`.) |
| 15 | Query contract | `{"question": String, "k"?: u64}`; `k` default `7`, must be `1..=20` else `400`; threshold fixed at `0.5`; empty result is `200 []`. |
| 16 | Keep-alive | SSE `KeepAlive` **interval** (default 15s, a named constant + optional config override). It repeats for the whole turn, so long model responses never "expire" at 15s. |

## Corrections to the original idea

- The `AgentBuilder... .add_hook(ToolHook{..}).tool(ReadFileTool::new())...`
  snippet is **not** rig 0.44's shape. In 0.44 the umbrella `rig` crate re-exports
  `rig-agent`; the surface is
  `agent.prompt(msg).history(msgs).stream()` → `StreamingResult`, hooks are the
  `AgentHook` trait (no `ToolHook` struct), and the `agent` cargo feature is on by
  default.
- `rig_core::serve` is an internal handler-dispatch/policy module, **not** an HTTP
  server. It is unrelated to the new `serve` subcommand.
- **The WASM client cannot depend on the `rig-rag` lib crate**: that crate depends
  on `rig`, `qdrant-client`, and `fastembed`, none of which compile to
  `wasm32-unknown-unknown`. Shared wire types must live in a new dependency-free
  `shared` crate (see layout).
- rig 0.44 streaming emits, in order: `StreamAssistantItem(Item::Event(StreamEvent::{Text|Reasoning|End|ToolCall|..}))`,
  then `ToolCall` / `ToolExecutionCommitted` / `ToolResult` (only with tools),
  `CompletionCall`, and finally `FinalResponse`. Text and reasoning arrive as
  deltas; there is no native "retrieved documents" event — hence decision #1.

## Wire contract (freeze in Phase 0)

Routes:

| Method | Path | Body | Response |
|--------|------|------|----------|
| GET | `/api/health` | — | `200 {"status":"ok"}` |
| POST | `/api/query` | `{"question": String, "k"?: u64}` | `200 [DocHit]` or `400` |
| POST | `/api/chat` | `{"history":[Msg], "prompt": String}` | `200 text/event-stream` or `400` |
| GET | `/*` | — | static files from `--site-dir` |

Types (in `shared`):

```rust
struct DocHit { score: f64, path: String, start_line: usize,
                end_line: usize, chunk_index: usize, text: String }
struct Msg { role: Role /* user | assistant */, content: String }

// SSE `event:` name is the variant; `data:` is the JSON of `ChatEvent`.
enum ChatEvent {
    Docs { docs: Vec<DocHit> },       // emitted once, after retrieval
    Thinking { text: String },        // reasoning deltas (may be absent)
    Delta { text: String },           // answer text deltas
    Final { text: String },           // full answer; server ends the body
    Error { message: String },        // server ends the body
    // reserved, never emitted in v1: ToolCall { .. }, ToolResult { .. }
}
```

SSE framing: `event: <name>\n data: <json>\n\n`, plus `KeepAlive` (~15s).
HTTP status contract: request-time validation (`k` out of range, malformed body)
is a `400` **before** any bytes of the stream. Once the stream starts, failures
are `Error` events followed by end-of-body.

Retrieval parameters (defaults, revisit if quality suffers): chat uses
`k = 7` and `threshold = 0.5` — same rules as `query`.

## Target layout

```
Cargo.toml            # add [workspace]; root package stays a member
Cargo.lock            # regenerate; Containerfile builds with --locked
shared/               # NEW: dependency-free wire types (wasm-safe)
  src/lib.rs          #   DocHit, Msg, ChatEvent, request bodies
src/
  retrieval.rs        # NEW: shared search fn extracted from query.rs
  serve/              # NEW module tree
    mod.rs            #   run(bind, site_dir): bind + serve
    state.rs          #   AppState; Retriever / Completer traits
    chat.rs           #   POST /api/chat -> SSE
    query.rs          #   POST /api/query
    health.rs
    prod.rs           #   QdrantRetriever, RigCompleter (map rig events)
    mock.rs           #   #[cfg(feature = "mock")] MockRetriever/MockCompleter
  bin/mock_server.rs  # NEW: real router + mocks, for Playwright
site/                 # NEW: Leptos CSR crate
  Cargo.toml src/ index.html Trunk.toml
  tests/              #   Playwright spec + package.json
tests/api_e2e.rs      # NEW: real router on port 0, reqwest + eventsource-stream
```

New deps (verify MSRV/versions at implementation time): `axum` (SSE needs no
feature; `json`/`tokio` are default), `tower-http` with `fs` (for `ServeDir`),
`futures`, `eventsource-stream` (SSE decode; used by the wasm client and the e2e
test), `reqwest` (wasm client + e2e client). **No** `axum/ws`,
`tokio-tungstenite`.

---

## Phase 0 — Contracts and seams (no server, no UI)

Deliverable: `shared` crate + traits defined; retrieval extracted; unit tests green.

Tasks
1. Add `[workspace]` (`members = ["shared", "site"]`; root is an implicit member)
   and the `shared` crate with `DocHit`, `Msg`, `ChatEvent`, request bodies;
   `serde` derive only — no heavy deps.
2. Define `Retriever` (`async fn retrieve(&self, question, k, threshold) -> Result<Vec<DocHit>>`)
   and `Completer` (returns a stream of `ChatEvent`, or a boxed stream of
   `Result<ChatEvent, _>`) in `src/serve/state.rs`, independent of rig types.
3. Extract `src/retrieval.rs` from the body of `query.rs`; rewrite `query.rs` to
   call it. Assert CLI output is byte-identical.
4. Prompt assembly: build the RAG preamble + retrieved context from `Vec<DocHit>`
   (own function, unit-tested).
5. History cap helper: `Vec<Msg>` → last N (unit-tested).

Acceptance / tests (units)
- `ChatEvent` / `DocHit` / `Msg` serde round-trip (incl. unknown-variant safety).
- `k` validation: `1..=20` ok; `0`, `21` rejected.
- threshold filter drops low-score hits; empty → empty vec.
- prompt assembly contains section paths/text of the hits, in order.
- history cap keeps exactly the last N, order preserved.

## Phase 1 — HTTP + SSE server, production impls

Deliverable: `rig-rag serve` serves `health`/`query`/`chat` against the real
Qdrant + OpenRouter stack; `curl` works end to end.

Tasks
1. `serve` subcommand in `main.rs`; `ServeArgs { bind, site_dir }`; optional
   `[server]` section in `config.rs` (`deny_unknown_fields` must stay; the
   section is optional, flags override).
2. `AppState { retriever: Arc<dyn Retriever>, completer: Arc<dyn Completer>,
   site_dir: PathBuf, keepalive: Duration }`; router builder `router(state)`.
3. `GET /api/health`, `POST /api/query` (validate `k` → 400; map hits).
4. `POST /api/chat`: validate body → 400; else `Sse::new(...).keep_alive(...)`,
   emitting `Docs` → `Thinking`/`Delta`* → `Final` | `Error`.
5. `prod.rs`:
   - `QdrantRetriever` wraps `store::connect(&config)` + `retrieval::search`.
   - `RigCompleter` builds the OpenRouter agent once (`AgentBuilder::new(llm)`
     with preamble), and per turn `agent.prompt(prompt).history(capped).stream()`,
     mapping `MultiTurnStreamItem` → our `ChatEvent` (`StreamAssistantItem(Event(Text))`
     → `Delta`, `Event(Reasoning)` → `Thinking`, `FinalResponse` → `Final`).
6. Startup: fail fast unless Qdrant + active collection + OpenRouter creds/model
   are all usable (decision #14). Log the bind address.
7. Serve static files from `site_dir` via `ServeDir` when the dir exists.

Acceptance
- `curl -s localhost:8080/api/health` → `{"status":"ok"}`.
- `curl -sN -X POST localhost:8080/api/chat -d '{...}'` shows ordered SSE frames.
- `k=0` / `k=21` → `400`; malformed JSON → `400`.
- Aborting the curl mid-stream stops the upstream rig stream (no orphan work).

## Phase 2 — Mock seam + API e2e

Deliverable: `cargo test` proves the API against the real router with mocks.

Tasks
1. `mock` cargo feature + `MockRetriever` / `MockCompleter` returning canned docs
   and deltas (deterministic, small sleeps to exercise streaming).
2. `src/bin/mock_server.rs`: real `router(state)` + mocks + static dir, chosen port.
3. `tests/api_e2e.rs`: bind port 0, get addr, drive with `reqwest` +
   `eventsource-stream`.

Acceptance / e2e (several)
- Query: happy path returns expected `DocHit`s; `k` bounds → 400.
- Chat: event order is `docs` → (`thinking`?) → `delta`* → `final`; `final.text`
  equals the concatenation of deltas.
- Chat with a completer that errors after the first delta → `error` event then
  clean body end (status already 200).
- Client disconnects mid-stream → server task ends without panic.

## Phase 3 — Leptos UI + static serving + dev loop

Deliverable: `trunk serve` UI talks to the API; `serve --site-dir site/dist`
serves the built UI.

Tasks
1. Scaffold `site/` (Leptos CSR + trunk), depending only on `shared`.
2. Tabs `Chat | Query`.
3. Shared composer: multiline textarea (~5–6 rows), Send button, **Enter submits**,
   **Shift+Enter inserts newline**.
4. Query page: numeric field `1..=20` (default 7); `POST /api/query`; render results
   as cards (3–4 lines collapsed, click to expand); empty-state card.
5. Chat page: `POST /api/chat`, read the body stream (`reqwest` wasm +
   `eventsource-stream`), render `docs` cards, collapsible `thinking`, streamed
   `delta` text, `error` bubble; keep client-side transcript for display and send
   it back next turn.
6. `Trunk.toml` dev proxy so `trunk serve` forwards `/api/*` to the axum server.

Acceptance
- Manual: both tabs work against the real stack; card expand and streaming render
  correctly; Enter/Shift+Enter behave.
- `trunk build --release` produces `site/dist/index.html` + wasm.

## Phase 4 — Site e2e (Playwright)

Deliverable: Playwright drives the real UI against `mock-server`.

Tasks
1. `site/tests/package.json` + Playwright config; `globalSetup` builds/launches
   `mock-server` (feature `mock`) on a fixed port and tears it down.
2. Specs (each mapped to a UI requirement).

Acceptance / e2e (several)
- Tabs switch; both pages render the composer.
- Enter submits; Shift+Enter adds a newline and does not submit.
- Streaming text appears incrementally and settles.
- Doc cards collapse to 3–4 lines and expand on click.
- Query `k` field enforces `1..=20`.
- Error state renders an error bubble.

## Phase 5 — Container + compose + docs

Deliverable: `compose up` starts Qdrant + the server, which serves API + site.

Tasks
1. Containerfile: add a WASM stage (`rustup target add wasm32-unknown-unknown`,
   install `trunk`) that builds `site/dist`; copy it into the runtime image at
   `/app/site/dist`. Keep the existing `rig-rag model` bake step.
2. `compose.yaml`: add a `server` service (same image) with
   `command: ["serve", "--bind", "0.0.0.0:8080", "--site-dir", "/app/site/dist"]`,
   `ports: ["8080:8080"]`, the same env (`QDRANT_URL`, `OPENROUTER_*`) and volumes,
   `depends_on: qdrant`. Leave `app` for `run --rm`.
3. README: new `serve` section, prerequisites (`wasm32-unknown-unknown`, `trunk`,
   `npx playwright install`), the `.env` requirement (decision #14), the port.
   Update the commands table.

Acceptance
- `podman compose up -d qdrant server` → UI reachable, chat streams.
- Documented prerequisites let a fresh clone run unit + api e2e + Playwright.

---

## Risks / watch items

- **rig 0.44 API churn.** The event mapping in Phase 1 step 5 is the most
  likely to need adjustment; verify variant names against the vendored source
  (`rig-agent-0.44.0/src/agent/streaming.rs`, `rig-core-0.44.0/src/streaming/event.rs`).
- **WASM dependency hygiene.** `shared` must stay free of native-only crates, or
  the `site` build breaks. Add a `cargo check -p shared --target wasm32-unknown-unknown`
  guard to the docs/CI later.
- **Toolchain weight.** `trunk` + wasm target + Playwright browsers are heavy;
  Phase 4 is the most likely to be deferred if the environment fights back.
- **Startup coupling** (decision #14): a missing OpenRouter key blocks the
  retrieval-only tab too. Revisit if that annoys in practice.
- **Embedding model load** happens at `serve` startup (~seconds); acceptable for
  a fail-fast server, but note it.

## Open defaults (change any of these without re-litigating the plan)

- Port `8080`; routes `/api/chat`, `/api/query`, `/api/health`.
- Keep-alive interval `15s` (constant + optional `[server].keep_alive_secs`).
- History cap `10` messages; chat retrieval `k = 7`, `threshold = 0.5`.
- Doc card: 3–4 lines collapsed, click to expand; `thinking` collapsible.
- `mock` feature name; `src/bin/mock_server.rs`; Rust e2e uses the in-process router.

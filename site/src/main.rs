//! WASM entry point for the rig-rag web UI (Leptos CSR + trunk).
//!
//! The UI speaks the same wire contract as the server (see the `shared` crate):
//! `POST /api/query` for retrieval and `POST /api/chat` for streamed answers.
//! In dev, `trunk serve` proxies `/api/*` to the axum server; in production the
//! server itself serves this `dist/`, so the client always uses relative URLs.

mod api;
mod app;
mod chat;
mod components;
mod query;

fn main() {
    leptos::mount::mount_to_body(app::App);
}

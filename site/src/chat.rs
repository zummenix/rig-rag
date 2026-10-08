//! Chat page: client-owned transcript over the streamed `POST /api/chat` endpoint.

use futures::StreamExt;
use leptos::prelude::*;
use leptos::task::spawn_local;
use shared::{ChatEvent, DocHit, Msg, Role};

use crate::api;
use crate::components::{Composer, DocList};

/// One rendered exchange. `done` is set once a `Final`/`Error` arrives (or the
/// stream ends); `error` short-circuits the answer bubble.
#[derive(Clone)]
struct Turn {
    /// Stable identity so `<For>` preserves per-turn view state while streaming.
    id: usize,
    prompt: String,
    docs: Vec<DocHit>,
    thinking: String,
    answer: String,
    error: Option<String>,
    done: bool,
}

/// RAG chat page backed by `POST /api/chat`.
///
/// Each turn is held in its own signal so streamed deltas update just that row
/// (and `<For>` can preserve its view state) instead of rebuilding the list.
#[component]
pub fn ChatPage() -> impl IntoView {
    let turns = RwSignal::new(Vec::<RwSignal<Turn>>::new());
    let busy = RwSignal::new(false);

    let on_submit = Callback::new(move |prompt: String| {
        if busy.get_untracked() {
            return;
        }

        // The client owns the transcript: replay completed exchanges as history.
        let history: Vec<Msg> = turns.with_untracked(|turns| {
            turns
                .iter()
                .flat_map(|turn| {
                    let turn = turn.get_untracked();
                    if !(turn.done && turn.error.is_none()) {
                        return Vec::new();
                    }
                    vec![
                        Msg {
                            role: Role::User,
                            content: turn.prompt.clone(),
                        },
                        Msg {
                            role: Role::Assistant,
                            content: turn.answer.clone(),
                        },
                    ]
                })
                .collect()
        });

        let id = turns.with(|turns| turns.len());
        let turn = RwSignal::new(Turn {
            id,
            prompt: prompt.clone(),
            docs: Vec::new(),
            thinking: String::new(),
            answer: String::new(),
            error: None,
            done: false,
        });
        turns.update(|turns| turns.push(turn));
        busy.set(true);

        spawn_local(async move {
            match api::chat(history, prompt).await {
                Ok(mut stream) => {
                    while let Some(item) = stream.next().await {
                        match item {
                            Ok(ChatEvent::Docs { docs }) => turn.update(|turn| turn.docs = docs),
                            Ok(ChatEvent::Thinking { text }) => {
                                turn.update(|turn| turn.thinking.push_str(&text));
                            }
                            Ok(ChatEvent::Delta { text }) => {
                                turn.update(|turn| turn.answer.push_str(&text));
                            }
                            Ok(ChatEvent::Final { text }) => {
                                turn.update(|turn| {
                                    turn.answer = text;
                                    turn.done = true;
                                });
                            }
                            Ok(ChatEvent::Error { message }) => {
                                turn.update(|turn| {
                                    turn.error = Some(message);
                                    turn.done = true;
                                });
                            }
                            // Reserved tool events and unknown variants are ignored in v1.
                            Ok(_) => {}
                            Err(message) => {
                                turn.update(|turn| {
                                    turn.error = Some(message);
                                    turn.done = true;
                                });
                            }
                        }
                    }
                    // Guard against a stream that ends without a terminal event.
                    turn.update(|turn| turn.done = true);
                }
                Err(message) => {
                    turn.update(|turn| {
                        turn.error = Some(message);
                        turn.done = true;
                    });
                }
            }
            busy.set(false);
        });
    });

    view! {
        <section class="panel chat">
            <div class="turns">
                <For each=move || turns.get() key=|turn| turn.with_untracked(|turn| turn.id) let:turn>
                    <TurnView turn=turn />
                </For>
            </div>
            <Composer
                busy=busy
                placeholder="Ask a question about the docs…"
                on_submit=on_submit
            />
        </section>
    }
}

/// Renders one exchange reactively from its own signal.
#[component]
fn TurnView(turn: RwSignal<Turn>) -> impl IntoView {
    view! {
        <div class="turn">
            <div class="bubble user">{move || turn.get().prompt}</div>

            {move || {
                let docs = turn.get().docs;
                (!docs.is_empty()).then(|| view! { <DocList docs=docs /> })
            }}

            {move || {
                let thinking = turn.get().thinking;
                (!thinking.is_empty())
                    .then(|| {
                        view! {
                            <details class="thinking">
                                <summary>"Thinking"</summary>
                                <pre>{thinking}</pre>
                            </details>
                        }
                    })
            }}

            {move || {
                let turn = turn.get();
                match turn.error {
                    Some(message) => {
                        view! { <div class="bubble error">{message}</div> }.into_any()
                    }
                    None if turn.answer.is_empty() && !turn.done => {
                        view! { <div class="bubble assistant muted">"…"</div> }.into_any()
                    }
                    None => {
                        view! {
                            <div class="bubble assistant">
                                {turn.answer}
                                <span class="cursor" class:hidden=turn.done>"▍"</span>
                            </div>
                        }
                            .into_any()
                    }
                }
            }}
        </div>
    }
}

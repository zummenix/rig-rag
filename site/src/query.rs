//! Query page: ask a question, retrieve the closest chunks, render them as cards.

use leptos::html;
use leptos::prelude::*;
use leptos::task::spawn_local;
use shared::{DocHit, MAX_K, MIN_K};

use crate::api;
use crate::components::{Composer, DocList};

/// Retrieval-only page backed by `POST /api/query`.
#[component]
pub fn QueryPage() -> impl IntoView {
    // `None` = no search yet; `Some(vec![])` = search returned nothing.
    let results = RwSignal::new(None::<Vec<DocHit>>);
    let error = RwSignal::new(None::<String>);
    let k_error = RwSignal::new(None::<String>);
    let loading = RwSignal::new(false);
    let k_input = NodeRef::<html::Input>::new();

    let on_submit = Callback::new(move |question: String| {
        let k = k_input
            .get()
            .and_then(|element| element.value().parse::<u64>().ok());
        let Some(k) = k else {
            k_error.set(Some("k must be a number".to_owned()));
            return;
        };
        if !(MIN_K..=MAX_K).contains(&k) {
            k_error.set(Some(format!("k must be between {MIN_K} and {MAX_K}")));
            return;
        }

        k_error.set(None);
        error.set(None);
        results.set(None);
        loading.set(true);

        spawn_local(async move {
            match api::query(question, k).await {
                Ok(hits) => results.set(Some(hits)),
                Err(message) => error.set(Some(message)),
            }
            loading.set(false);
        });
    });

    view! {
        <section class="panel">
            <div class="query-controls">
                <label>
                    "Results (k) "
                    <input node_ref=k_input type="number" min="1" max="20" value="7" />
                </label>
                <span class="field-hint">"1–20"</span>
            </div>

            {move || k_error.get().map(|message| view! { <p class="error inline">{message}</p> })}

            <Composer
                busy=loading
                placeholder="Enter a query to search in the docs…"
                on_submit=on_submit
                should_reset_on_submit=false
            />

            {move || error.get().map(|message| view! { <div class="bubble error">{message}</div> })}

            {move || {
                if loading.get() {
                    view! { <p class="muted">"Searching…"</p> }.into_any()
                } else {
                    match results.get() {
                        None => ().into_any(),
                        Some(hits) if hits.is_empty() => {
                            view! { <div class="empty-card">"No matching documents."</div> }
                                .into_any()
                        }
                        Some(hits) => view! { <DocList docs=hits /> }.into_any(),
                    }
                }
            }}
        </section>
    }
}

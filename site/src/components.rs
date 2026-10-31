//! Reusable view pieces: the shared message composer and a retrieved-doc card.

use std::rc::Rc;

use leptos::html;
use leptos::prelude::*;
use shared::DocHit;
use wasm_bindgen::JsCast;

/// Multiline composer shared by both pages.
///
/// Enter submits; Shift+Enter inserts a newline. The value is read straight off
/// the DOM node so the caret is never reset by a reactive round-trip.
#[component]
pub fn Composer(
    busy: RwSignal<bool>,
    placeholder: &'static str,
    on_submit: Callback<String>,
    should_reset_on_submit: bool,
) -> impl IntoView {
    let textarea = NodeRef::<html::Textarea>::new();

    let submit = Rc::new(move || {
        if busy.get_untracked() {
            return;
        }
        let Some(element) = textarea.get() else {
            return;
        };
        let value = element.value();
        let question = value.trim();
        if question.is_empty() {
            return;
        }
        on_submit.run(question.to_owned());
        if should_reset_on_submit {
            element.set_value("");
        }
    });

    let submit_on_click = submit.clone();
    let submit_on_key = submit.clone();

    view! {
        <div class="composer">
            <textarea
                class="composer-input"
                node_ref=textarea
                rows="5"
                placeholder=placeholder
                on:keydown=move |event: web_sys::KeyboardEvent| {
                    if event.key() == "Enter" && !event.shift_key() {
                        event.prevent_default();
                        submit_on_key();
                    }
                }
            ></textarea>
            <button
                class="composer-send"
                disabled=move || busy.get()
                on:click=move |_| submit_on_click()
            >
                {move || if busy.get() { "Sending…" } else { "Send" }}
            </button>
        </div>
    }
}

/// The retrieved chunks for one chat turn, grouped under a single collapsible
/// section. Each chunk has its own collapsible details element.
///
/// The expansion state lives in signals owned by these components, and the
/// parent keeps them mounted while the turn streams, so answer deltas do not
/// rebuild the section and collapse it again.
#[component]
pub fn ChatDocs(docs: Memo<Vec<DocHit>>) -> impl IntoView {
    let expanded = RwSignal::new(false);

    view! {
        <Show when=move || !docs.get().is_empty()>
            <details
                class="chat-docs"
                open=move || expanded.get()
                on:toggle=move |event: web_sys::Event| {
                    if let Some(details) = event
                        .target()
                        .and_then(|target| target.dyn_into::<web_sys::HtmlDetailsElement>().ok())
                    {
                        expanded.set(details.open());
                    }
                }
            >
                <summary class="chat-docs-summary">
                    <span class="chat-docs-label">"Found documents"</span>
                    <span class="chat-docs-count">{move || docs.get().len()}</span>
                </summary>
                <div class="doc-list">
                    <For
                        each=move || docs.get()
                        key=|doc| (doc.path.clone(), doc.chunk_index)
                        let:doc
                    >
                        <ChatDoc doc=doc />
                    </For>
                </div>
            </details>
        </Show>
    }
}

/// One retrieved chunk inside [`ChatDocs`], collapsible on its own.
#[component]
fn ChatDoc(doc: DocHit) -> impl IntoView {
    let expanded = RwSignal::new(false);
    let location = doc.source_location();
    let score = format!("{:.2}", doc.score);
    let text = doc.text;

    view! {
        <details
            class="chat-doc"
            open=move || expanded.get()
            on:toggle=move |event: web_sys::Event| {
                if let Some(details) = event
                    .target()
                    .and_then(|target| target.dyn_into::<web_sys::HtmlDetailsElement>().ok())
                {
                    expanded.set(details.open());
                }
            }
        >
            <summary class="chat-doc-summary">
                <span class="doc-path">{location}</span>
                <span class="doc-score">{score}</span>
            </summary>
            <p class="doc-text">{text}</p>
        </details>
    }
}

/// One retrieved chunk: source location + score header, text clamped to a few
/// lines until clicked.
#[component]
pub fn DocCard(doc: DocHit) -> impl IntoView {
    let expanded = RwSignal::new(false);
    let location = doc.source_location();
    let score = format!("{:.2}", doc.score);
    let text = doc.text;

    view! {
        <article
            class="doc-card"
            class:expanded=move || expanded.get()
            on:click=move |_| expanded.update(|value| *value = !*value)
        >
            <header class="doc-card-header">
                <span class="doc-path">{location}</span>
                <span class="doc-score">{score}</span>
            </header>
            <p class="doc-text">{text}</p>
        </article>
    }
}

/// A vertical list of [`DocCard`]s, preserving retrieval order.
#[component]
pub fn DocList(docs: Vec<DocHit>) -> impl IntoView {
    view! {
        <div class="doc-list">
            {docs.into_iter().map(|doc| view! { <DocCard doc=doc /> }).collect::<Vec<_>>()}
        </div>
    }
}

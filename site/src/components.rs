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

/// Streaming reasoning trace.
///
/// While collapsed, the summary shows the most recent non-empty line of the
/// stream and updates as new reasoning text arrives. Expanding the block hides
/// that preview and reveals the full trace.
#[component]
pub fn Thinking(text: Signal<String>) -> impl IntoView {
    let expanded = RwSignal::new(false);
    let last_line = Memo::new(move |_| {
        text.get()
            .lines()
            .rev()
            .map(str::trim)
            .find(|line| !line.is_empty())
            .unwrap_or_default()
            .to_owned()
    });

    view! {
        <details
            class="thinking"
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
            <summary class="thinking-summary">
                <span class="thinking-label">"Thinking"</span>
                <span class="thinking-last-line">{move || last_line.get()}</span>
            </summary>
            <pre>{move || text.get()}</pre>
        </details>
    }
}

/// Retrieval results for the Search tab.
///
/// Unlike the chat sources, these are always expanded: the full chunk text is
/// the point of a search result, so the cards are prominent and non-collapsible.
#[component]
pub fn SearchResults(docs: Vec<DocHit>) -> impl IntoView {
    view! {
        <div class="search-results">
            {docs
                .into_iter()
                .enumerate()
                .map(|(index, doc)| view! { <SearchResultCard rank=index + 1 doc=doc /> })
                .collect::<Vec<_>>()}
        </div>
    }
}

/// One always-open search hit with its rank, source location, and full text.
#[component]
fn SearchResultCard(rank: usize, doc: DocHit) -> impl IntoView {
    let location = doc.source_location();
    let score = format!("{:.2}", doc.score);
    let text = doc.text;

    view! {
        <article class="search-result">
            <header class="search-result-header">
                <span class="search-rank">"#" {rank}</span>
                <span class="search-path">{location}</span>
                <span class="search-score">{score}</span>
            </header>
            <p class="search-text">{text}</p>
        </article>
    }
}

//! App shell: the `Chat | Query` tab switcher.
//!
//! Both pages stay mounted (hidden via `display`) so each keeps its state when
//! the user switches tabs.

use leptos::prelude::*;

use crate::chat::ChatPage;
use crate::query::QueryPage;

#[derive(Clone, Copy, PartialEq, Eq)]
enum Tab {
    Chat,
    Query,
}

/// Root component, mounted into `<body>`.
#[component]
pub fn App() -> impl IntoView {
    let tab = RwSignal::new(Tab::Chat);

    view! {
        <div class="app">
            <header class="app-header">
                <h1>"rig-rag"</h1>
                <nav class="tabs">
                    <button
                        class="tab"
                        class:active=move || tab.get() == Tab::Chat
                        on:click=move |_| tab.set(Tab::Chat)
                    >
                        "Chat"
                    </button>
                    <button
                        class="tab"
                        class:active=move || tab.get() == Tab::Query
                        on:click=move |_| tab.set(Tab::Query)
                    >
                        "Query"
                    </button>
                </nav>
            </header>
            <main class="content">
                <div style:display=move || {
                    if tab.get() == Tab::Chat { "block" } else { "none" }
                }>
                    <ChatPage />
                </div>
                <div style:display=move || {
                    if tab.get() == Tab::Query { "block" } else { "none" }
                }>
                    <QueryPage />
                </div>
            </main>
        </div>
    }
}

use anyhow::Result;
use futures::{StreamExt, TryStreamExt, future::BoxFuture};
use rig_agent::AgentBuilder;
use rig_agent::agent::MultiTurnStreamItem;
use rig_core::completion::message::Message;
use rig_core::providers::openai::{
    OpenAIConfig,
    wire::{Auth, Dialect, OLLAMA, OPENAI, OPENROUTER, Quirks},
};
use rig_core::streaming::{Item, StreamEvent};

use crate::config::{Config, LlmConfig, LlmProvider};
use crate::retrieval;
use crate::serve::chat::PREAMBLE;
use crate::serve::state::{ChatStream, Completer, Retriever};
use crate::store::Store;
use shared::{ChatEvent, DocHit, Msg, Role, SearchMode};

/// Generic OpenAI-compatible gateway: OpenAI quirks without a streamed usage
/// chunk. Optional bearer auth is set on the config in [`RigCompleter::connect`].
/// Not a registered rig dialect, so it is never serialized.
const CUSTOM: Dialect = Dialect::gateway("custom", "", "LLM_API_KEY")
    .with_quirks(Quirks::openai().without_stream_usage());

/// [`Retriever`] backed by the active Qdrant collection.
pub struct QdrantRetriever {
    store: Store,
}

impl QdrantRetriever {
    /// Connects and verifies the active collection (fails fast at startup).
    /// `log_eval_metrics` enables structured per-query eval logging.
    pub async fn connect(config: &Config, log_eval_metrics: bool) -> Result<Self> {
        let store = Store::connect(config, log_eval_metrics).await?;
        Ok(Self { store })
    }
}

impl Retriever for QdrantRetriever {
    fn retrieve<'a>(
        &'a self,
        question: &'a str,
        k: u64,
        mode: SearchMode,
    ) -> BoxFuture<'a, Result<Vec<DocHit>>> {
        Box::pin(retrieval::search(&self.store, question, k, mode))
    }
}

/// [`Completer`] backed by a rig agent over the provider in `[llm]`.
pub struct RigCompleter {
    agent: rig_agent::Agent,
    identity: String,
}

impl RigCompleter {
    /// Builds the agent once (fails fast at startup when the credentials or
    /// model name are missing). The provider, model and base URL are kept for
    /// first-turn error context.
    pub fn connect(llm: &LlmConfig) -> Result<Self> {
        let dialect = match llm.provider {
            LlmProvider::Openrouter => &OPENROUTER,
            LlmProvider::Openai => &OPENAI,
            LlmProvider::Ollama => &OLLAMA,
            LlmProvider::Custom => &CUSTOM,
        };
        let key = llm
            .read_key(|name| std::env::var(name).ok())?
            .unwrap_or_default();
        let model = llm.model.clone();
        let base_url = llm.base_url().to_owned();
        let agent = AgentBuilder::new(
            OpenAIConfig::with_key(dialect, key)
                .with_base_url(base_url.clone())
                .with_auth(Auth::OptionalBearer)
                .client()
                .chat(&model)
                .erase(),
        )
        .preamble(PREAMBLE)
        .build();
        let identity = format!(
            "provider={} model={} base_url={}",
            llm.provider.as_str(),
            model,
            base_url
        );
        Ok(Self { agent, identity })
    }
}

impl Completer for RigCompleter {
    fn complete<'a>(
        &'a self,
        prompt: String,
        history: Vec<Msg>,
    ) -> BoxFuture<'a, Result<ChatStream>> {
        Box::pin(async move {
            let history = history.into_iter().map(message_of).collect::<Vec<_>>();
            let identity = self.identity.clone();
            let stream = self
                .agent
                .prompt(Message::user(prompt))
                .history(history)
                .stream();
            let mapped = stream
                .map_err(move |error| {
                    anyhow::Error::new(error).context(format!("llm request failed ({})", identity))
                })
                .filter_map(|item| async move {
                    Some(match item {
                        Ok(MultiTurnStreamItem::StreamAssistantItem(Item::Event(
                            StreamEvent::Text { text, .. },
                        ))) => Ok(ChatEvent::Delta { text }),
                        Ok(MultiTurnStreamItem::StreamAssistantItem(Item::Event(
                            StreamEvent::Reasoning { text, .. },
                        ))) => Ok(ChatEvent::Thinking { text }),
                        Ok(MultiTurnStreamItem::FinalResponse(response)) => Ok(ChatEvent::Final {
                            text: response.output(),
                        }),
                        Err(error) => Err(error),
                        _ => return None,
                    })
                });
            let stream: ChatStream = Box::pin(mapped);
            Ok(stream)
        })
    }
}

/// Converts a client transcript message into a rig message.
fn message_of(msg: Msg) -> Message {
    match msg.role {
        Role::User => Message::user(msg.content),
        Role::Assistant => Message::assistant(msg.content),
    }
}

use anyhow::{Context, Result, anyhow};
use futures::{StreamExt, future::BoxFuture};
use rig_agent::AgentBuilder;
use rig_agent::agent::MultiTurnStreamItem;
use rig_core::completion::message::Message;
use rig_core::providers::openrouter;
use rig_core::streaming::{Item, StreamEvent};

use crate::config::Config;
use crate::retrieval;
use crate::serve::chat::PREAMBLE;
use crate::serve::state::{ChatStream, Completer, Retriever};
use crate::store::Store;
use shared::{ChatEvent, DocHit, Msg, Role, SearchMode};

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

/// [`Completer`] backed by a rig agent over OpenRouter.
pub struct RigCompleter {
    agent: rig_agent::Agent,
}

impl RigCompleter {
    /// Builds the OpenRouter agent once (fails fast at startup when the
    /// credentials or model name are missing).
    pub fn connect() -> Result<Self> {
        let client = openrouter::from_env()
            .map_err(|error| anyhow!("failed to create OpenRouter client: {error}"))?;
        let model_name =
            std::env::var("OPENROUTER_MODEL_NAME").context("OPENROUTER_MODEL_NAME not set")?;
        let llm = client.completion(model_name);
        let agent = AgentBuilder::new(llm).preamble(PREAMBLE).build();
        Ok(Self { agent })
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
            let stream = self
                .agent
                .prompt(Message::user(prompt))
                .history(history)
                .stream();
            let mapped = stream.filter_map(|item| async move {
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
                    Err(error) => Err(anyhow::Error::new(error)),
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

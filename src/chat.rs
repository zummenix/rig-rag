use anyhow::Result;
use rig::AgentBuilder;
use rig::integrations::cli_chatbot::ChatBotBuilder;
use rig::providers::openrouter;

use crate::config::Config;
use crate::store;

const CONTEXT_SAMPLES: usize = 7;

/// Launches the interactive RAG chatbot backed by the active collection.
pub async fn run() -> Result<()> {
    let config = Config::load()?;
    let vector_store = store::connect(&config).await?;

    let client = openrouter::from_env()
        .unwrap_or_else(|e| panic!("Failed to create OpenRouter client: {e}"));
    let model_name = std::env::var("OPENROUTER_MODEL_NAME").expect("OPENROUTER_MODEL_NAME not set");
    let llm = client.completion(model_name);

    let rag_agent = AgentBuilder::new(llm)
        .preamble("You are a helpful assistant that answers questions about techincal docs")
        .dynamic_context(CONTEXT_SAMPLES, vector_store)
        .build();

    let chatbot = ChatBotBuilder::new().agent(rag_agent).build();
    chatbot.run().await?;

    Ok(())
}

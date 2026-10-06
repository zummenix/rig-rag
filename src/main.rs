use anyhow::Result;
use rig::embeddings::EmbeddingsBuilder;
use rig::integrations::cli_chatbot::ChatBotBuilder;
use rig::loaders::FileLoader;
use rig::providers::openrouter;
use rig::vector_store::in_memory_store::InMemoryVectorStore;
use rig::{AgentBuilder, fastembed};
use tokio;

#[tokio::main]
async fn main() -> Result<()> {
    let docs = FileLoader::with_glob("data/**/*.md")?
        .read_with_path()
        .ignore_errors()
        .into_iter();

    println!("Loaded documents count: {}", docs.size_hint().0);

    let fastembed_model = fastembed::FastembedModel::BGESmallENV15;
    let embedding_model =
        fastembed::Fastembed::load(&fastembed_model)?.embedding(&fastembed_model, None)?;

    let embeddings = EmbeddingsBuilder::new(embedding_model.clone())
        .documents(docs.map(|(_, text)| text))?
        .build()
        .await?;

    println!("Prepared embeddings count: {}", embeddings.len());

    let vector_store = InMemoryVectorStore::from_documents(embeddings);

    println!("Initialized vector store");

    let index = vector_store.index(embedding_model);

    println!("Prepared vector index");

    let client = openrouter::from_env()
        .unwrap_or_else(|e| panic!("Failed to create OpenRouter client: {e}"));
    let model_name = std::env::var("OPENROUTER_MODEL_NAME").expect("OPENROUTER_MODEL_NAME not set");
    let llm = client.completion(model_name);

    let rag_agent = AgentBuilder::new(llm)
        .preamble("You are a helpful assistant that answers questions about techincal docs")
        .dynamic_context(4, index)
        .build();

    let chatbot = ChatBotBuilder::new().agent(rag_agent).build();
    chatbot.run().await?;

    Ok(())
}

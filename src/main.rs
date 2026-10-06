use std::path::PathBuf;

use anyhow::Result;
use rig::embeddings::EmbeddingsBuilder;
use rig::integrations::cli_chatbot::ChatBotBuilder;
use rig::loaders::FileLoader;
use rig::providers::openrouter;
use rig::vector_store::in_memory_store::InMemoryVectorStore;
use rig::vector_store::{VectorSearchRequest, VectorStoreIndex};
use rig::{AgentBuilder, Embed, fastembed};
use tokio;

#[tokio::main]
async fn main() -> Result<()> {
    let docs = FileLoader::with_glob("data/**/*.md")?
        .read_with_path()
        .ignore_errors()
        .into_iter()
        .collect::<Vec<_>>();

    println!("Loaded documents count: {}", docs.iter().count());

    let fastembed_model = fastembed::FastembedModel::BGESmallENV15;
    let embedding_model =
        fastembed::Fastembed::load(&fastembed_model)?.embedding(&fastembed_model, None)?;

    let embeddings = EmbeddingsBuilder::new(embedding_model.clone())
        .documents(docs.into_iter().map(chunk_md_doc).flatten())?
        .build()
        .await?;

    println!("Prepared embeddings count: {}", embeddings.len());

    let vector_store = InMemoryVectorStore::from_documents_with_ids(
        embeddings
            .into_iter()
            .map(|(doc_chunk, embeddings)| (doc_chunk.id, doc_chunk.text, embeddings)),
    );
    let index = vector_store.index(embedding_model);

    println!("Initialized and prepared vector store");

    // let client = openrouter::from_env()
    //     .unwrap_or_else(|e| panic!("Failed to create OpenRouter client: {e}"));
    // let model_name = std::env::var("OPENROUTER_MODEL_NAME").expect("OPENROUTER_MODEL_NAME not set");
    // let llm = client.completion(model_name);

    // let rag_agent = AgentBuilder::new(llm)
    //     .preamble("You are a helpful assistant that answers questions about techincal docs")
    //     .dynamic_context(4, index)
    //     .build();

    // let chatbot = ChatBotBuilder::new().agent(rag_agent).build();
    // chatbot.run().await?;

    let req = VectorSearchRequest::builder()
        .query("Who are authors of jj?")
        .samples(7)
        .build();

    let hits = index.top_n::<String>(req).await?;

    for hit in &hits {
        let score = hit.0;
        let id = &hit.1;
        let payload = hit
            .2
            .lines()
            .take(6)
            .map(|str| str.to_owned())
            .collect::<Vec<String>>()
            .join("\n");
        println!("Score: {score}");
        println!("ID: {id}");
        println!("\n{payload}\n...\n");
    }

    Ok(())
}

fn chunk_md_doc((path, doc): (PathBuf, String)) -> Vec<DocChunk> {
    chunkedrs::chunk(&doc)
        .markdown()
        .split()
        .into_iter()
        .map(|chunk| {
            let id = format!(
                "{}:[{}-{}]",
                path.to_string_lossy(),
                chunk.start_byte,
                chunk.end_byte
            );
            DocChunk {
                id,
                text: chunk.content,
            }
        })
        .collect()
}

#[derive(Eq, PartialEq, Debug)]
struct DocChunk {
    id: String,
    text: String,
}

impl Embed for DocChunk {
    fn embed(
        &self,
        embedder: &mut rig::embeddings::TextEmbedder,
    ) -> std::prelude::v1::Result<(), rig::embeddings::EmbedError> {
        embedder.embed(self.text.clone());
        Ok(())
    }
}

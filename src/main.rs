use std::path::PathBuf;

use anyhow::Result;
use qdrant_client::{
    Qdrant,
    qdrant::{CreateCollectionBuilder, Distance, QueryPointsBuilder, VectorParamsBuilder},
};
use rig::embeddings::EmbeddingsBuilder;
use rig::integrations::cli_chatbot::ChatBotBuilder;
use rig::loaders::FileLoader;
use rig::providers::openrouter;
use rig::vector_store::in_memory_store::InMemoryVectorStore;
use rig::vector_store::{InsertDocuments, VectorSearchRequest, VectorStoreIndex};
use rig::{AgentBuilder, Embed, fastembed};
use rig_qdrant::QdrantVectorStore;
use serde::{Deserialize, Serialize};
use tokio;

const COLLECTION_NAME: &str = "docs";

#[tokio::main]
async fn main() -> Result<()> {
    let fastembed_model = fastembed::FastembedModel::BGESmallENV15;

    let embedding_model =
        fastembed::Fastembed::load(&fastembed_model)?.embedding(&fastembed_model, None)?;
    let qdrant = Qdrant::from_url("http://localhost:6334").build()?;

    let vector_store = if !qdrant.collection_exists(COLLECTION_NAME).await? {
        let dims = 384; // TODO: we need to get this value from fastembed somehow!
        qdrant
            .create_collection(
                CreateCollectionBuilder::new(COLLECTION_NAME)
                    .vectors_config(VectorParamsBuilder::new(dims, Distance::Cosine)),
            )
            .await?;
        let docs = FileLoader::with_glob("data/**/*.md")?
            .read_with_path()
            .ignore_errors()
            .into_iter()
            .collect::<Vec<_>>();

        println!("Loaded documents count: {}", docs.iter().count());

        let embeddings = EmbeddingsBuilder::new(embedding_model.clone())
            .documents(docs.into_iter().map(chunk_md_doc).flatten())?
            .build()
            .await?;

        println!("Prepared embeddings count: {}", embeddings.len());

        let query_params = QueryPointsBuilder::new(COLLECTION_NAME).with_payload(true);
        let vector_store = QdrantVectorStore::new(qdrant, embedding_model, query_params.build());

        vector_store.insert_documents(embeddings).await?;

        vector_store
    } else {
        let query_params = QueryPointsBuilder::new(COLLECTION_NAME).with_payload(true);
        QdrantVectorStore::new(qdrant, embedding_model, query_params.build())
    };

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

    let hits = vector_store.top_n::<DocChunk>(req).await?;

    for hit in &hits {
        let score = hit.0;
        let id = &hit.1;
        let payload = hit
            .2
            .text
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
    print!("{}", path.to_string_lossy());
    let chunks = chunkedrs::chunk(&doc)
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
        .collect::<Vec<_>>();
    println!(": {}", chunks.len());
    chunks
}

#[derive(Eq, PartialEq, Debug, Serialize, Deserialize)]
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

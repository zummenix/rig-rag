use anyhow::Result;
use qdrant_client::{
    Qdrant,
    qdrant::{CreateCollectionBuilder, Distance, VectorParamsBuilder},
};
use rig::embeddings::EmbeddingsBuilder;
use rig::loaders::FileLoader;
use rig::vector_store::InsertDocuments;

use crate::chunk::chunk_md_doc;
use crate::{embedding, store};

/// Embeds every markdown document under `data/` and upserts the chunks into
/// Qdrant, creating the collection when it is missing.
pub async fn run() -> Result<()> {
    let model = embedding::load()?;
    let dims = model.capabilities().ndims;

    let client = Qdrant::from_url(store::QDRANT_URL).build()?;
    if !client.collection_exists(store::COLLECTION_NAME).await? {
        client
            .create_collection(
                CreateCollectionBuilder::new(store::COLLECTION_NAME)
                    .vectors_config(VectorParamsBuilder::new(dims as u64, Distance::Cosine)),
            )
            .await?;
    }

    let docs = FileLoader::with_glob("data/**/*.md")?
        .read_with_path()
        .ignore_errors()
        .into_iter()
        .collect::<Vec<_>>();

    println!("Loaded documents count: {}", docs.len());

    let embeddings = EmbeddingsBuilder::new(model.clone())
        .documents(docs.into_iter().flat_map(chunk_md_doc))?
        .build()
        .await?;

    println!("Prepared embeddings count: {}", embeddings.len());

    let vector_store = store::new_store(client, model);

    println!("Inserting into the vector store");

    let mut progress = 0;
    for embeddings_page in embeddings.chunks(100) {
        progress += embeddings_page.len();
        vector_store
            .insert_documents(embeddings_page.to_vec())
            .await?;
        println!("{progress}");
    }

    Ok(())
}

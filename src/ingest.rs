use std::path::{Path, PathBuf};

use anyhow::Result;
use rig::embeddings::EmbeddingsBuilder;
use rig::loaders::FileLoader;
use rig::vector_store::InsertDocuments;

use crate::chunk::chunk_md_doc;
use crate::config::Config;
use crate::sources::{self, Sources};
use crate::{embedding, fetch, hashing, stats, store};

const DOCUMENTS_BATCH_SIZE: usize = 10;

/// Fetches every configured source, hashes it, and embeds the whole corpus into
/// a collection named after the corpus prefix, model, and combined hash. Existing
/// collections are reused untouched unless `force` is set. With `pin`, branch and
/// tag refs in the sources file are rewritten to the resolved commit SHAs.
pub async fn run(
    force: bool,
    pin: bool,
    config_path: impl AsRef<Path>,
    sources_path: impl AsRef<Path>,
) -> Result<()> {
    let config = Config::load(&config_path)?;
    let sources = Sources::load(&sources_path)?;
    let data_root = &config.corpus.data_root;
    let prefix = config.corpus.prefix.as_str();

    println!("Fetching {} source(s)", sources.iter().count());
    let fetched = fetch::fetch_all(&sources, data_root)?;

    if pin {
        let refs: Vec<(String, String)> = fetched
            .iter()
            .map(|source| (source.name.clone(), source.commit.clone()))
            .collect();
        sources::write_refs(&sources_path, &refs)?;
        println!(
            "Pinned {} source ref(s) to resolved commits in {}",
            refs.len(),
            sources_path.as_ref().display()
        );
    }

    let hashes = hashing::hash_sources(&sources, data_root)?;
    for (name, hash) in &hashes {
        println!("  {name}: {}", &hash[..12]);
    }
    let combined = hashing::combine(
        hashes
            .iter()
            .map(|(name, hash)| (name.as_str(), hash.as_str())),
    );
    let target = hashing::collection_name(prefix, &config.embedding.model, &combined);
    println!("Target collection: {target}");

    let client = store::client(&config.qdrant.url)?;
    let exists = client.collection_exists(&target).await?;
    if exists && !force {
        println!("Collection {target} already exists; nothing to do. Use --force to rebuild.");
        stats::report_memory();
        return Ok(());
    }
    if exists {
        println!("--force: deleting existing collection {target}");
        store::delete_collection(&client, &target).await?;
    }

    let model = embedding::load_slug(&config.embedding.model)?;
    let dims = model.capabilities().ndims as u64;
    store::create_collection(&client, &target, dims).await?;

    let documents = load_documents(&sources, data_root)?;
    println!("Loaded documents count: {}", documents.len());

    let mut total_embeddings_count = 0;
    let vector_store = store::new_store(client, model.clone(), &target);
    for documents_batch in documents.chunks(DOCUMENTS_BATCH_SIZE) {
        let embeddings = EmbeddingsBuilder::new(model.clone())
            .documents(documents_batch.iter().flat_map(chunk_md_doc))?
            .build()
            .await?;
        total_embeddings_count += embeddings.len();
        vector_store.insert_documents(embeddings).await?;
    }

    println!("Prepared embeddings count: {}", total_embeddings_count);
    println!("Ingested into {target}.");
    println!("Promote with: rig-rag promote {target}");
    stats::report_memory();
    Ok(())
}

/// Loads markdown documents from each configured source directory. The corpus is
/// exactly the configured sources, so a source removed from sources.json is
/// never read even if its stale directory still exists on disk.
fn load_documents(
    sources: &Sources,
    data_root: impl AsRef<Path>,
) -> Result<Vec<(PathBuf, String)>> {
    let data_root = data_root.as_ref();
    let mut documents = Vec::new();
    for source in sources.iter() {
        let pattern = format!("{}/**/*.md", source.dir(data_root).display());
        documents.extend(
            FileLoader::with_glob(&pattern)?
                .read_with_path()
                .ignore_errors(),
        );
    }
    Ok(documents)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn load_documents_ignores_unlisted_directories() {
        let root = std::env::temp_dir().join(format!("rig-rag-ingest-{}", std::process::id()));
        let cwd = std::env::current_dir().unwrap();
        std::fs::create_dir_all(root.join("data/jj/docs")).unwrap();
        std::fs::create_dir_all(root.join("data/stale")).unwrap();
        std::fs::write(root.join("data/jj/docs/a.md"), "# a").unwrap();
        std::fs::write(root.join("data/stale/b.md"), "# b").unwrap();

        let sources =
            Sources::parse(r#"[{"name":"jj","type":"git","url":"u","path":"docs"}]"#).unwrap();

        std::env::set_current_dir(&root).unwrap();
        let documents = load_documents(&sources, "data").unwrap();
        std::env::set_current_dir(&cwd).unwrap();

        assert_eq!(documents.len(), 1);
        assert!(documents[0].0.ends_with("a.md"));
        let _ = std::fs::remove_dir_all(&root);
    }
}

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::time::Instant;

use anyhow::Result;
use rig::embeddings::EmbeddingsBuilder;
use rig::loaders::FileLoader;
use rig::vector_store::InsertDocuments;

use crate::chunk::{DocChunk, chunk_md_doc};
use crate::config::Config;
use crate::report;
use crate::sources::{self, Sources};
use crate::{embedding, fetch, hashing, stats, store};

const DOCUMENTS_BATCH_SIZE: usize = 10;

/// Fetches every configured source, hashes it, and embeds the whole corpus into
/// a collection named after the corpus prefix, model, and combined hash. Existing
/// collections are reused untouched unless `force` is set. With `pin`, branch and
/// tag refs in the sources file are rewritten to the resolved commit SHAs.
///
/// With `report`, a schema-versioned JSON report (timings, memory, per-source
/// counts, chunk-token distribution) is written. Nothing extra — in particular
/// no tokenization or per-phase timing — runs when `report` is `None`.
pub async fn run(
    force: bool,
    pin: bool,
    report_path: Option<PathBuf>,
    config_path: impl AsRef<Path>,
    sources_path: impl AsRef<Path>,
) -> Result<()> {
    let report_enabled = report_path.is_some();
    let mut timers = Timers::new(report_enabled);

    let config_path = config_path.as_ref();
    let sources_path = sources_path.as_ref();
    let config = Config::load(config_path)?;
    let sources = Sources::load(sources_path)?;
    let data_root = &config.corpus.data_root;
    let prefix = config.corpus.prefix.as_str();

    timers.start();
    println!("Fetching {} source(s)", sources.iter().count());
    let fetched = fetch::fetch_all(&sources, data_root)?;
    timers.fetch = timers.stop();

    if pin {
        let refs: Vec<(String, String)> = fetched
            .iter()
            .map(|source| (source.name.clone(), source.commit.clone()))
            .collect();
        sources::write_refs(sources_path, &refs)?;
        println!(
            "Pinned {} source ref(s) to resolved commits in {}",
            refs.len(),
            sources_path.display()
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
    let status = match (exists, force) {
        (true, false) => report::Status::Reused,
        (true, true) => report::Status::Rebuilt,
        (false, _) => report::Status::Created,
    };

    let inputs = ReportInputs {
        config_path,
        sources_path,
        config: &config,
        sources: &sources,
        fetched: &fetched,
        target: &target,
        force,
    };

    if exists && !force {
        println!("Collection {target} already exists; nothing to do. Use --force to rebuild.");
        stats::report_memory();
        if let Some(path) = report_path.as_deref() {
            let model = embedding::load_slug(&config.embedding.model)?;
            let dimensions = model.capabilities().ndims as u64;
            write_report(&inputs, status, timers.timing(), dimensions, None, 0, path)?;
            println!("Wrote ingest report to {}", path.display());
        }
        return Ok(());
    }

    if exists {
        println!("--force: deleting existing collection {target}");
        store::delete_collection(&client, &target).await?;
    }

    let model = embedding::load_slug(&config.embedding.model)?;
    let dims = model.capabilities().ndims as u64;
    store::create_collection(&client, &target, dims).await?;

    timers.start();
    let documents = load_documents(&sources, data_root)?;
    timers.load = timers.stop();
    println!("Loaded documents count: {}", documents.len());

    let source_index: HashMap<&str, usize> = sources
        .iter()
        .enumerate()
        .map(|(index, source)| (source.name.as_str(), index))
        .collect();
    let mut ingest_stats = report_enabled.then(|| IngestStats::new(sources.iter().count()));

    let mut total_embeddings_count = 0usize;
    let mut chunk_ms = 0u64;
    let mut embed_ms = 0u64;
    let mut insert_ms = 0u64;
    let vector_store = store::new_store(client, model.clone(), &target);
    for documents_batch in documents.chunks(DOCUMENTS_BATCH_SIZE) {
        timers.start();
        let mut batch_chunks: Vec<DocChunk> = Vec::new();
        for document in documents_batch {
            let chunks = chunk_md_doc(&document.path, &document.text);
            if let Some(ingest_stats) = ingest_stats.as_mut() {
                let index = source_index[document.source.as_str()];
                ingest_stats.documents[index] += 1;
                ingest_stats.chunks[index] += chunks.len();
                for chunk in &chunks {
                    ingest_stats.tokens[index].push(report::count_tokens(&chunk.text));
                }
            }
            batch_chunks.extend(chunks);
        }
        chunk_ms += timers.stop();

        timers.start();
        let embeddings = EmbeddingsBuilder::new(model.clone())
            .documents(batch_chunks)?
            .build()
            .await?;
        embed_ms += timers.stop();
        total_embeddings_count += embeddings.len();

        timers.start();
        vector_store.insert_documents(embeddings).await?;
        insert_ms += timers.stop();
    }
    timers.chunk = chunk_ms;
    timers.embed = embed_ms;
    timers.insert = insert_ms;

    println!("Prepared embeddings count: {}", total_embeddings_count);
    println!("Ingested into {target}.");
    println!(
        "Promote with: rig-rag --config {} promote {target}",
        config_path.display()
    );
    stats::report_memory();

    if let Some(path) = report_path.as_deref() {
        write_report(
            &inputs,
            status,
            timers.timing(),
            dims,
            ingest_stats.as_ref(),
            total_embeddings_count,
            path,
        )?;
        println!("Wrote ingest report to {}", path.display());
    }
    Ok(())
}

/// Wall-clock phase timers. All measurement is skipped when `enabled` is false,
/// so a run without `--report` does no timing work.
struct Timers {
    enabled: bool,
    mark: Instant,
    started: Instant,
    fetch: u64,
    load: u64,
    chunk: u64,
    embed: u64,
    insert: u64,
}

impl Timers {
    fn new(enabled: bool) -> Self {
        let now = Instant::now();
        Self {
            enabled,
            mark: now,
            started: now,
            fetch: 0,
            load: 0,
            chunk: 0,
            embed: 0,
            insert: 0,
        }
    }

    fn start(&mut self) {
        if self.enabled {
            self.mark = Instant::now();
        }
    }

    fn stop(&mut self) -> u64 {
        if self.enabled {
            self.mark.elapsed().as_millis() as u64
        } else {
            0
        }
    }

    fn timing(&self) -> report::Timing {
        report::Timing {
            fetch: self.fetch,
            load: self.load,
            chunk: self.chunk,
            embed: self.embed,
            insert: self.insert,
            total: if self.enabled {
                self.started.elapsed().as_millis() as u64
            } else {
                0
            },
        }
    }
}

/// Per-source accumulation for the report, indexed by source order.
struct IngestStats {
    documents: Vec<usize>,
    chunks: Vec<usize>,
    tokens: Vec<Vec<usize>>,
}

impl IngestStats {
    fn new(source_count: usize) -> Self {
        Self {
            documents: vec![0; source_count],
            chunks: vec![0; source_count],
            tokens: vec![Vec::new(); source_count],
        }
    }
}

/// Identity shared by every report field except the run outcome.
struct ReportInputs<'a> {
    config_path: &'a Path,
    sources_path: &'a Path,
    config: &'a Config,
    sources: &'a Sources,
    fetched: &'a [fetch::Fetched],
    target: &'a str,
    force: bool,
}

/// Serializes and writes the report to `path`.
fn write_report(
    inputs: &ReportInputs<'_>,
    status: report::Status,
    timing: report::Timing,
    dimensions: u64,
    ingest_stats: Option<&IngestStats>,
    embeddings: usize,
    path: &Path,
) -> Result<()> {
    let report = assemble_report(inputs, status, timing, dimensions, ingest_stats, embeddings);
    report::write(path, &report)
}

fn assemble_report(
    inputs: &ReportInputs<'_>,
    status: report::Status,
    timing: report::Timing,
    dimensions: u64,
    ingest_stats: Option<&IngestStats>,
    embeddings: usize,
) -> report::IngestReport {
    let resolved: HashMap<&str, &str> = inputs
        .fetched
        .iter()
        .map(|source| (source.name.as_str(), source.commit.as_str()))
        .collect();

    let empty: [usize; 0] = [];
    let mut sources_detail = Vec::with_capacity(inputs.sources.iter().count());
    let mut total_documents = 0usize;
    let mut total_chunks = 0usize;
    let mut total_tokens = 0usize;
    for (index, source) in inputs.sources.iter().enumerate() {
        let token_counts: &[usize] = match ingest_stats {
            Some(stats) => &stats.tokens[index],
            None => &empty,
        };
        let documents = ingest_stats.map_or(0, |stats| stats.documents[index]);
        let chunks = ingest_stats.map_or(0, |stats| stats.chunks[index]);
        let tokens: usize = token_counts.iter().sum();

        sources_detail.push(report::SourceDetail {
            name: source.name.clone(),
            url: source.url.clone(),
            r#ref: source.r#ref.clone(),
            resolved_commit: resolved
                .get(source.name.as_str())
                .copied()
                .unwrap_or_default()
                .to_string(),
            documents,
            chunks,
            tokens,
            chunk_tokens: report::chunk_tokens(token_counts),
        });
        total_documents += documents;
        total_chunks += chunks;
        total_tokens += tokens;
    }

    report::IngestReport {
        schema_version: report::SCHEMA_VERSION,
        status,
        profile: report::profile_of(inputs.config_path),
        config: inputs.config_path.display().to_string(),
        sources: inputs.sources_path.display().to_string(),
        collection: inputs.target.to_string(),
        prefix: inputs.config.corpus.prefix.clone(),
        force: inputs.force,
        model: report::ModelInfo {
            slug: inputs.config.embedding.model.clone(),
            dimensions,
        },
        timing_ms: timing,
        memory: report::memory(),
        tokenizer: Some(report::TOKENIZER.to_string()),
        sources_detail,
        totals: report::Totals {
            documents: total_documents,
            chunks: total_chunks,
            tokens: total_tokens,
            embeddings,
        },
        environment: report::environment(&inputs.config.qdrant.url),
    }
}

/// A markdown document loaded from a source directory, tagged with its source.
struct LoadedDocument {
    source: String,
    path: PathBuf,
    text: String,
}

/// Loads markdown documents from each configured source directory. The corpus is
/// exactly the configured sources, so a source removed from sources.json is
/// never read even if its stale directory still exists on disk.
fn load_documents(sources: &Sources, data_root: impl AsRef<Path>) -> Result<Vec<LoadedDocument>> {
    let data_root = data_root.as_ref();
    let mut documents = Vec::new();
    for source in sources.iter() {
        let pattern = format!("{}/**/*.md", source.dir(data_root).display());
        let loaded = FileLoader::with_glob(&pattern)?
            .read_with_path()
            .ignore_errors();
        for (path, text) in loaded {
            documents.push(LoadedDocument {
                source: source.name.clone(),
                path,
                text,
            });
        }
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
        assert!(documents[0].path.ends_with("a.md"));
        assert_eq!(documents[0].source, "jj");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn assemble_report_aggregates_per_source_stats() {
        let config = Config::parse(
            r#"
[qdrant]
url = "http://localhost:6334"

[embedding]
model = "bge-small-en-v1.5"

[collection]
active = ""

[corpus]
prefix = "eval-single-project"
data_root = "data/single-project"
"#,
        )
        .unwrap();
        let sources =
            Sources::parse(r#"[{"name":"jj","type":"git","url":"u","path":"docs"}]"#).unwrap();
        let fetched = vec![fetch::Fetched {
            name: "jj".to_string(),
            commit: "a".repeat(40),
        }];
        let inputs = ReportInputs {
            config_path: Path::new("eval/profiles/single-project/rig-rag.toml"),
            sources_path: Path::new("eval/profiles/single-project/sources.json"),
            config: &config,
            sources: &sources,
            fetched: &fetched,
            target: "eval-single-project-bge-small-en-v1-5-0123456789ab",
            force: false,
        };

        let stats = IngestStats {
            documents: vec![2],
            chunks: vec![3],
            tokens: vec![vec![10, 60, 150]],
        };
        let report = assemble_report(
            &inputs,
            report::Status::Created,
            report::Timing::default(),
            384,
            Some(&stats),
            3,
        );

        assert_eq!(report.profile, "single-project");
        assert_eq!(report.prefix, "eval-single-project");
        assert_eq!(report.totals.documents, 2);
        assert_eq!(report.totals.chunks, 3);
        assert_eq!(report.totals.tokens, 220);
        assert_eq!(report.totals.embeddings, 3);
        assert_eq!(report.sources_detail.len(), 1);
        assert_eq!(report.sources_detail[0].resolved_commit, "a".repeat(40));
        assert_eq!(report.sources_detail[0].chunk_tokens.max, 150);

        let reused = assemble_report(
            &inputs,
            report::Status::Reused,
            report::Timing::default(),
            384,
            None,
            0,
        );
        assert_eq!(reused.totals.chunks, 0);
        assert_eq!(reused.sources_detail[0].documents, 0);
        assert_eq!(reused.sources_detail[0].chunk_tokens.max, 0);
    }
}

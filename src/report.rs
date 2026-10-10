use std::path::Path;

use anyhow::{Context, Result};
use serde::Serialize;

use crate::stats;

/// Version of the `ingest --report` JSON contract. Bump when fields change in a
/// way consumers must know about; the eval layer keys compatibility off this.
pub const SCHEMA_VERSION: u32 = 1;

/// Tokenizer identity recorded in the report. `cl100k_base` is not BGE's
/// WordPiece vocabulary, but it is a stable, cheap approximation for relative
/// token/cost comparisons.
pub const TOKENIZER: &str = "tiktoken:cl100k_base";

/// Encoding passed to `tiktoken::get_encoding`.
const TOKENIZER_ENCODING: &str = "cl100k_base";

/// The full `ingest --report` document. Mirrors the schema in
/// `docs/eval-plan.md`; additions are backward-compatible, removals bump
/// [`SCHEMA_VERSION`].
#[derive(Debug, Serialize)]
pub struct IngestReport {
    pub schema_version: u32,
    pub status: Status,
    pub profile: String,
    pub config: String,
    pub sources: String,
    pub collection: String,
    pub prefix: String,
    pub force: bool,
    /// Minimum chunk-token count applied during this ingest; `0` keeps every
    /// chunk. Recorded so the report states exactly what was filtered.
    pub min_chunk_tokens: usize,
    pub model: ModelInfo,
    pub timing_ms: Timing,
    pub memory: Memory,
    /// `None` when tokens were not computed; always set for a written report.
    pub tokenizer: Option<String>,
    pub sources_detail: Vec<SourceDetail>,
    pub totals: Totals,
    pub environment: Environment,
}

/// What happened to the target collection during this run.
#[derive(Debug, Clone, Copy, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Status {
    Created,
    Reused,
    Rebuilt,
}

#[derive(Debug, Serialize)]
pub struct ModelInfo {
    pub slug: String,
    pub dimensions: u64,
}

/// Per-phase wall-clock timings, in milliseconds. All zero when no report was
/// requested, since nothing is measured then.
#[derive(Debug, Default, Serialize)]
pub struct Timing {
    pub fetch: u64,
    pub load: u64,
    pub chunk: u64,
    pub embed: u64,
    pub insert: u64,
    pub total: u64,
}

#[derive(Debug, Serialize)]
pub struct Memory {
    pub peak_rss_bytes: Option<u64>,
    pub cgroup_peak_bytes: Option<u64>,
}

/// Per-source counts. `tokens` is the sum over chunks; `chunk_tokens` describes
/// the distribution (feeds the "drop small chunks" experiment).
#[derive(Debug, Serialize)]
pub struct SourceDetail {
    pub name: String,
    pub url: String,
    pub r#ref: String,
    pub resolved_commit: String,
    pub documents: usize,
    pub chunks: usize,
    pub tokens: usize,
    pub chunk_tokens: ChunkTokens,
}

#[derive(Debug, Serialize)]
pub struct ChunkTokens {
    pub min: usize,
    pub max: usize,
    pub mean: f64,
    pub p50: usize,
    pub p90: usize,
    pub buckets: ChunkTokenBuckets,
}

/// Histogram of chunk sizes in tokens. Every key names an explicit inclusive
/// integer range, so no boundary is left to interpretation: `<50` covers 0–49,
/// `50-99`, `100-199`, `200-399`, and `>=400` covers 400 and up. Exactly 50,
/// 100, 200, and 400 land in the bucket whose lower bound they equal. The
/// serialized keys match the `eval-plan.md` schema.
#[derive(Debug, Default, Serialize)]
pub struct ChunkTokenBuckets {
    #[serde(rename = "<50")]
    pub lt_50: usize,
    #[serde(rename = "50-99")]
    pub from_50_to_99: usize,
    #[serde(rename = "100-199")]
    pub from_100_to_199: usize,
    #[serde(rename = "200-399")]
    pub from_200_to_399: usize,
    #[serde(rename = ">=400")]
    pub gte_400: usize,
}

#[derive(Debug, Serialize)]
pub struct Totals {
    pub documents: usize,
    pub chunks: usize,
    pub tokens: usize,
    pub embeddings: usize,
}

#[derive(Debug, Serialize)]
pub struct Environment {
    pub os: String,
    pub arch: String,
    pub qdrant_url: String,
}

/// Counts tokens in `text` with the report tokenizer. Only call this when a
/// report is being written; it is the one intentionally expensive step.
pub fn count_tokens(text: &str) -> usize {
    tiktoken::get_encoding(TOKENIZER_ENCODING)
        .expect("cl100k_base is compiled into tiktoken")
        .count(text)
}

/// Summarizes a source's per-chunk token counts. An empty corpus yields an
/// all-zero summary rather than panicking.
pub fn chunk_tokens(counts: &[usize]) -> ChunkTokens {
    if counts.is_empty() {
        return ChunkTokens {
            min: 0,
            max: 0,
            mean: 0.0,
            p50: 0,
            p90: 0,
            buckets: ChunkTokenBuckets::default(),
        };
    }

    let mut sorted = counts.to_vec();
    sorted.sort_unstable();
    let sum: usize = sorted.iter().sum();

    let mut buckets = ChunkTokenBuckets::default();
    for &count in counts {
        if count < 50 {
            buckets.lt_50 += 1;
        } else if count < 100 {
            buckets.from_50_to_99 += 1;
        } else if count < 200 {
            buckets.from_100_to_199 += 1;
        } else if count < 400 {
            buckets.from_200_to_399 += 1;
        } else {
            buckets.gte_400 += 1;
        }
    }

    ChunkTokens {
        min: sorted[0],
        max: sorted[sorted.len() - 1],
        mean: sum as f64 / sorted.len() as f64,
        p50: percentile(&sorted, 50.0),
        p90: percentile(&sorted, 90.0),
        buckets,
    }
}

/// Nearest-rank percentile over an already-sorted slice.
fn percentile(sorted: &[usize], p: f64) -> usize {
    debug_assert!(!sorted.is_empty());
    let rank = ((p / 100.0) * sorted.len() as f64).ceil() as usize;
    let index = rank.saturating_sub(1).min(sorted.len() - 1);
    sorted[index]
}

/// Captures the environment block for a report against `qdrant_url`.
pub fn environment(qdrant_url: &str) -> Environment {
    Environment {
        os: std::env::consts::OS.to_string(),
        arch: std::env::consts::ARCH.to_string(),
        qdrant_url: qdrant_url.to_string(),
    }
}

/// Best-effort memory block: peak RSS and container cgroup peak.
pub fn memory() -> Memory {
    Memory {
        peak_rss_bytes: stats::peak_rss_bytes(),
        cgroup_peak_bytes: stats::cgroup_peak_bytes(),
    }
}

/// Derives the corpus profile name from the config path: the parent directory
/// name (`eval/profiles/single-project/rig-rag.toml` -> `single-project`). An
/// empty string for a config in the working directory.
pub fn profile_of(config_path: &Path) -> String {
    config_path
        .parent()
        .and_then(|parent| parent.file_name())
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default()
}

/// Writes `report` to `path` as pretty JSON, creating parent directories.
pub fn write(path: &Path, report: &IngestReport) -> Result<()> {
    if let Some(parent) = path.parent()
        && !parent.as_os_str().is_empty()
    {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("failed to create {}", parent.display()))?;
    }
    let mut json =
        serde_json::to_string_pretty(report).context("failed to serialize ingest report")?;
    json.push('\n');
    std::fs::write(path, json)
        .with_context(|| format!("failed to write ingest report at {}", path.display()))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn counts_tokens_for_text() {
        assert_eq!(count_tokens(""), 0);
        assert!(count_tokens("hello world") > 0);
    }

    #[test]
    fn empty_chunk_tokens_is_all_zero() {
        let summary = chunk_tokens(&[]);
        assert_eq!(summary.min, 0);
        assert_eq!(summary.max, 0);
        assert_eq!(summary.mean, 0.0);
        assert_eq!(summary.p50, 0);
        assert_eq!(summary.p90, 0);
        assert_eq!(summary.buckets.lt_50, 0);
    }

    #[test]
    fn chunk_tokens_buckets_and_percentiles() {
        let counts = [10, 60, 150, 300, 500];
        let summary = chunk_tokens(&counts);
        assert_eq!(summary.min, 10);
        assert_eq!(summary.max, 500);
        assert_eq!(summary.mean, 204.0);
        assert_eq!(summary.p50, 150);
        assert_eq!(summary.p90, 500);
        assert_eq!(summary.buckets.lt_50, 1);
        assert_eq!(summary.buckets.from_50_to_99, 1);
        assert_eq!(summary.buckets.from_100_to_199, 1);
        assert_eq!(summary.buckets.from_200_to_399, 1);
        assert_eq!(summary.buckets.gte_400, 1);
    }

    #[test]
    fn chunk_tokens_bucket_boundaries_match_inclusive_ranges() {
        let counts = [49, 50, 99, 100, 199, 200, 399, 400];
        let summary = chunk_tokens(&counts);
        // Each boundary value lands in the bucket whose lower bound it equals.
        assert_eq!(summary.buckets.lt_50, 1); // 49
        assert_eq!(summary.buckets.from_50_to_99, 2); // 50, 99
        assert_eq!(summary.buckets.from_100_to_199, 2); // 100, 199
        assert_eq!(summary.buckets.from_200_to_399, 2); // 200, 399
        assert_eq!(summary.buckets.gte_400, 1); // 400
    }

    #[test]
    fn profile_of_uses_parent_directory() {
        assert_eq!(
            profile_of(Path::new("eval/profiles/single-project/rig-rag.toml")),
            "single-project"
        );
        assert_eq!(profile_of(Path::new("rig-rag.toml")), "");
    }

    #[test]
    fn serializes_schema_version_and_fields() {
        let report = IngestReport {
            schema_version: SCHEMA_VERSION,
            status: Status::Reused,
            profile: "single-project".to_string(),
            config: "eval/profiles/single-project/rig-rag.toml".to_string(),
            sources: "eval/profiles/single-project/sources.json".to_string(),
            collection: "eval-single-project-bge-small-en-v1-5-0123456789ab".to_string(),
            prefix: "eval-single-project".to_string(),
            force: false,
            min_chunk_tokens: 0,
            model: ModelInfo {
                slug: "bge-small-en-v1.5".to_string(),
                dimensions: 384,
            },
            timing_ms: Timing::default(),
            memory: Memory {
                peak_rss_bytes: Some(1),
                cgroup_peak_bytes: None,
            },
            tokenizer: Some(TOKENIZER.to_string()),
            sources_detail: vec![SourceDetail {
                name: "jj".to_string(),
                url: "https://github.com/jj-vcs/jj".to_string(),
                r#ref: "main".to_string(),
                resolved_commit: "a".repeat(40),
                documents: 1,
                chunks: 2,
                tokens: 3,
                chunk_tokens: chunk_tokens(&[10, 60]),
            }],
            totals: Totals {
                documents: 1,
                chunks: 2,
                tokens: 3,
                embeddings: 2,
            },
            environment: Environment {
                os: "macos".to_string(),
                arch: "aarch64".to_string(),
                qdrant_url: "http://localhost:6334".to_string(),
            },
        };

        let value: serde_json::Value = serde_json::to_value(&report).unwrap();
        assert_eq!(value["schema_version"], SCHEMA_VERSION);
        assert_eq!(value["status"], "reused");
        assert_eq!(value["min_chunk_tokens"], 0);
        assert_eq!(value["model"]["dimensions"], 384);
        assert_eq!(value["sources_detail"][0]["ref"], "main");
        assert_eq!(
            value["sources_detail"][0]["chunk_tokens"]["buckets"]["<50"],
            1
        );
        assert_eq!(
            value["sources_detail"][0]["chunk_tokens"]["buckets"][">=400"],
            0
        );
        assert_eq!(value["totals"]["embeddings"], 2);
    }

    #[test]
    fn writes_pretty_json_creating_parents() {
        let dir = std::env::temp_dir().join(format!("rig-rag-report-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let path = dir.join("nested/report.json");
        let report = IngestReport {
            schema_version: SCHEMA_VERSION,
            status: Status::Created,
            profile: String::new(),
            config: "rig-rag.toml".to_string(),
            sources: "sources.json".to_string(),
            collection: "docs-x".to_string(),
            prefix: "docs".to_string(),
            force: false,
            min_chunk_tokens: 0,
            model: ModelInfo {
                slug: "bge-small-en-v1.5".to_string(),
                dimensions: 384,
            },
            timing_ms: Timing::default(),
            memory: Memory {
                peak_rss_bytes: None,
                cgroup_peak_bytes: None,
            },
            tokenizer: Some(TOKENIZER.to_string()),
            sources_detail: Vec::new(),
            totals: Totals {
                documents: 0,
                chunks: 0,
                tokens: 0,
                embeddings: 0,
            },
            environment: environment("http://localhost:6334"),
        };

        write(&path, &report).unwrap();

        let raw = std::fs::read_to_string(&path).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&raw).unwrap();
        assert_eq!(parsed["schema_version"], SCHEMA_VERSION);
        assert!(raw.ends_with('\n'));
        let _ = std::fs::remove_dir_all(&dir);
    }
}

use std::path::Path;

use anyhow::{Context, Result};
use serde::Deserialize;

/// Path of the runtime configuration, relative to the working directory.
pub const CONFIG_PATH: &str = "rig-rag.toml";

/// Environment variable overriding `qdrant.url`, so containers can point the
/// tool at a service name without editing the config.
const QDRANT_URL_ENV: &str = "QDRANT_URL";

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub qdrant: QdrantConfig,
    pub embedding: EmbeddingConfig,
    pub collection: CollectionConfig,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct QdrantConfig {
    pub url: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EmbeddingConfig {
    /// Model slug, e.g. `bge-small-en-v1.5`.
    pub model: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CollectionConfig {
    /// Alias (or collection) the commands read from and write to.
    pub active: String,
}

impl Config {
    /// Loads [`CONFIG_PATH`], applying the [`QDRANT_URL_ENV`] override.
    pub fn load() -> Result<Self> {
        Self::from_path(CONFIG_PATH)
    }

    pub fn from_path(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        let raw = std::fs::read_to_string(path)
            .with_context(|| format!("failed to read config at {}", path.display()))?;
        Ok(Self::parse(&raw)?.with_qdrant_override(std::env::var(QDRANT_URL_ENV).ok()))
    }

    pub fn parse(raw: &str) -> Result<Self> {
        toml::from_str(raw).context("failed to parse config")
    }

    fn with_qdrant_override(mut self, url: Option<String>) -> Self {
        if let Some(url) = url {
            self.qdrant.url = url;
        }
        self
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE: &str = r#"
[qdrant]
url = "http://localhost:6334"

[embedding]
model = "bge-small-en-v1.5"

[collection]
active = "docs"
"#;

    #[test]
    fn parses_all_fields() {
        let config = Config::parse(SAMPLE).unwrap();
        assert_eq!(config.qdrant.url, "http://localhost:6334");
        assert_eq!(config.embedding.model, "bge-small-en-v1.5");
        assert_eq!(config.collection.active, "docs");
    }

    #[test]
    fn rejects_missing_section() {
        assert!(Config::parse("[qdrant]\nurl = \"x\"\n").is_err());
    }

    #[test]
    fn rejects_unknown_field() {
        assert!(Config::parse(&format!("{SAMPLE}\nextra = 1\n")).is_err());
    }

    #[test]
    fn override_replaces_qdrant_url() {
        let config = Config::parse(SAMPLE)
            .unwrap()
            .with_qdrant_override(Some("http://qdrant:6334".into()));
        assert_eq!(config.qdrant.url, "http://qdrant:6334");
    }
}

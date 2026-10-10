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
    /// Defaults for the server; CLI flags override each field.
    pub server: Option<ServerConfig>,
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
    /// Name of the active collection. Empty until a collection is promoted.
    pub active: String,
}

/// Optional `[server]` section; absent entirely means all defaults apply.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ServerConfig {
    /// Address to bind, e.g. `127.0.0.1:8080`.
    pub bind: Option<String>,
    /// Directory of built UI assets to serve.
    pub site_dir: Option<std::path::PathBuf>,
    /// SSE keep-alive interval in seconds.
    pub keep_alive_secs: Option<u64>,
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

/// Rewrites `[collection].active` in `path`, preserving comments and layout.
pub fn write_active(path: impl AsRef<Path>, collection: &str) -> Result<()> {
    let path = path.as_ref();
    let raw = std::fs::read_to_string(path)
        .with_context(|| format!("failed to read config at {}", path.display()))?;
    let mut document = raw
        .parse::<toml_edit::DocumentMut>()
        .context("failed to parse config")?;
    document["collection"]["active"] = toml_edit::value(collection);
    std::fs::write(path, document.to_string())
        .with_context(|| format!("failed to write config at {}", path.display()))?;
    Ok(())
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
active = ""
"#;

    #[test]
    fn parses_all_fields() {
        let config = Config::parse(SAMPLE).unwrap();
        assert_eq!(config.qdrant.url, "http://localhost:6334");
        assert_eq!(config.embedding.model, "bge-small-en-v1.5");
        assert_eq!(config.collection.active, "");
        assert!(config.server.is_none());
    }

    #[test]
    fn parses_optional_server_section() {
        let raw = format!(
            "{SAMPLE}\n[server]\nbind = \"0.0.0.0:9000\"\nsite_dir = \"site/dist\"\nkeep_alive_secs = 20\n"
        );
        let config = Config::parse(&raw).unwrap();
        let server = config.server.expect("server section");
        assert_eq!(server.bind.as_deref(), Some("0.0.0.0:9000"));
        assert_eq!(server.site_dir.as_deref(), Some(Path::new("site/dist")));
        assert_eq!(server.keep_alive_secs, Some(20));
    }

    #[test]
    fn rejects_unknown_server_field() {
        let raw = format!("{SAMPLE}\n[server]\nbogus = 1\n");
        assert!(Config::parse(&raw).is_err());
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

    #[test]
    fn write_active_preserves_comments_and_updates_value() {
        let dir = std::env::temp_dir().join(format!("rig-rag-config-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("rig-rag.toml");
        std::fs::write(
            &path,
            "# keep me\n[qdrant]\nurl = \"u\"\n\n[embedding]\nmodel = \"m\"\n\n[collection]\nactive = \"\"\n",
        )
        .unwrap();

        write_active(&path, "docs-x").unwrap();

        let written = std::fs::read_to_string(&path).unwrap();
        assert!(written.contains("# keep me"));
        assert_eq!(Config::parse(&written).unwrap().collection.active, "docs-x");
        let _ = std::fs::remove_dir_all(&dir);
    }
}

use std::path::Path;

use anyhow::{Context, Result};
use serde::Deserialize;

/// Path of the runtime configuration, relative to the working directory.
pub const CONFIG_PATH: &str = "rig-rag.toml";

/// Default collection-name prefix, and the production value.
pub const DEFAULT_CORPUS_PREFIX: &str = "docs";

/// Default directory fetched sources are checked out under.
pub const DEFAULT_DATA_ROOT: &str = "data";

/// Environment variable overriding `qdrant.url`, so containers can point the
/// tool at a service name without editing the config.
const QDRANT_URL_ENV: &str = "QDRANT_URL";

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub qdrant: QdrantConfig,
    pub embedding: EmbeddingConfig,
    pub collection: CollectionConfig,
    /// Corpus naming and layout. Absent entirely means production defaults
    /// (prefix `docs`, data root `data`), so existing configs are unchanged.
    #[serde(default)]
    pub corpus: CorpusConfig,
    /// Optional cloud reranker. Absent entirely means retrieval without
    /// reranking.
    pub reranker: Option<RerankerConfig>,
    /// Defaults for the server; CLI flags override each field.
    pub server: Option<ServerConfig>,
    /// Optional chat LLM. Absent entirely means chat is disabled.
    pub llm: Option<LlmConfig>,
}

/// Optional `[reranker]` section for a cloud `/v1/rerank` endpoint.
///
/// The authorization token is deliberately not a field: `rig-rag.toml` is
/// tracked, so the token is read from [`RERANKER_API_KEY`] instead.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RerankerConfig {
    /// API root including the version segment, e.g. `https://api.jina.ai/v1`.
    /// The rerank endpoint is `{base_url}/rerank`.
    pub base_url: String,
    /// Reranker model id, e.g. `rerank-3`.
    pub model: String,
    /// Retrieved candidates scored per query before cutting to `k`.
    #[serde(default = "default_rerank_candidates")]
    pub candidates: u64,
    /// Per-request timeout. Cloud rerankers can take seconds per call.
    #[serde(default = "default_rerank_timeout_ms")]
    pub timeout_ms: u64,
}

/// Environment variable holding the reranker authorization token.
pub const RERANKER_API_KEY: &str = "RERANKER_API_KEY";

/// Default number of retrieved candidates sent to the reranker. The fused
/// ranking is rank-biased, so the pool is deliberately deeper than the default
/// `k` to give the reranker a chance at candidates a single retriever ranks low.
pub const DEFAULT_RERANK_CANDIDATES: u64 = 60;
/// Default per-request reranker timeout.
pub const DEFAULT_RERANK_TIMEOUT_MS: u64 = 10_000;

fn default_rerank_candidates() -> u64 {
    DEFAULT_RERANK_CANDIDATES
}

fn default_rerank_timeout_ms() -> u64 {
    DEFAULT_RERANK_TIMEOUT_MS
}

impl RerankerConfig {
    /// The authorization token, or an error naming the environment variable.
    pub fn api_key(&self) -> Result<String> {
        let token = std::env::var(RERANKER_API_KEY).with_context(|| {
            format!("[reranker] is configured, but {RERANKER_API_KEY} is not set")
        })?;
        if token.trim().is_empty() {
            anyhow::bail!("[reranker] is configured, but {RERANKER_API_KEY} is empty");
        }
        Ok(token.trim().to_owned())
    }
}

/// Optional `[corpus]` section controlling collection prefixes and where
/// fetched sources live. Both fields default to the production values.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CorpusConfig {
    /// Prefix of every collection this config creates, e.g. `docs`.
    #[serde(default = "default_prefix")]
    pub prefix: String,
    /// Directory fetched sources are checked out under, e.g. `data`.
    #[serde(default = "default_data_root")]
    pub data_root: std::path::PathBuf,
}

impl Default for CorpusConfig {
    fn default() -> Self {
        Self {
            prefix: default_prefix(),
            data_root: default_data_root(),
        }
    }
}

fn default_prefix() -> String {
    DEFAULT_CORPUS_PREFIX.to_string()
}

fn default_data_root() -> std::path::PathBuf {
    std::path::PathBuf::from(DEFAULT_DATA_ROOT)
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
    /// Loads the config at `path`, applying the [`QDRANT_URL_ENV`] override.
    pub fn load(path: impl AsRef<Path>) -> Result<Self> {
        Self::from_path(path)
    }

    pub fn from_path(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        let raw = std::fs::read_to_string(path)
            .with_context(|| format!("failed to read config at {}", path.display()))?;
        Ok(Self::parse(&raw)?.with_qdrant_override(std::env::var(QDRANT_URL_ENV).ok()))
    }

    pub fn parse(raw: &str) -> Result<Self> {
        let mut config: Config = toml::from_str(raw).context("failed to parse config")?;
        config.validate_llm()?;
        Ok(config)
    }

    /// Validates the optional `[llm]` section.
    fn validate_llm(&mut self) -> Result<()> {
        let Some(llm) = self.llm.as_mut() else {
            return Ok(());
        };
        let model = llm.model.trim();
        if model.is_empty() {
            anyhow::bail!("[llm].model must not be empty");
        }
        llm.model = model.to_owned();
        if let Some(base_url) = llm.base_url.as_mut() {
            let trimmed = base_url.trim().trim_end_matches('/');
            if trimmed.is_empty() {
                anyhow::bail!("[llm].base_url must not be empty");
            }
            *base_url = trimmed.to_owned();
        }
        if llm.provider == LlmProvider::Custom && llm.base_url.is_none() {
            anyhow::bail!("[llm].base_url is required for provider 'custom'");
        }
        Ok(())
    }

    fn with_qdrant_override(mut self, url: Option<String>) -> Self {
        if let Some(url) = url {
            self.qdrant.url = url;
        }
        self
    }
}

/// Chat provider, from `[llm].provider`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum LlmProvider {
    Openai,
    Openrouter,
    Ollama,
    Custom,
}

impl LlmProvider {
    /// Provider name as a static string, for logs and errors.
    pub fn as_str(&self) -> &'static str {
        match self {
            LlmProvider::Openai => "openai",
            LlmProvider::Openrouter => "openrouter",
            LlmProvider::Ollama => "ollama",
            LlmProvider::Custom => "custom",
        }
    }
}

/// Optional `[llm]` section selecting the chat model.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LlmConfig {
    pub provider: LlmProvider,
    /// Provider-native model id, used verbatim.
    pub model: String,
    /// Optional endpoint override; defaulted per provider, required for `custom`.
    pub base_url: Option<String>,
}

impl LlmConfig {
    /// Resolved chat endpoint. Defaults per provider; `custom` uses its own
    /// required value.
    pub fn base_url(&self) -> &str {
        if let Some(base_url) = self.base_url.as_deref() {
            return base_url;
        }
        match self.provider {
            LlmProvider::Openrouter => "https://openrouter.ai/api/v1",
            LlmProvider::Openai => "https://api.openai.com/v1",
            LlmProvider::Ollama => "http://localhost:11434/v1",
            // Validation requires `base_url` for `custom`.
            LlmProvider::Custom => {
                unreachable!("[llm].base_url is validated for provider 'custom'")
            }
        }
    }

    /// Environment variable holding the provider's authorization token.
    pub fn api_key_env(&self) -> &'static str {
        match self.provider {
            LlmProvider::Openrouter => "OPENROUTER_API_KEY",
            LlmProvider::Openai => "OPENAI_API_KEY",
            LlmProvider::Ollama => "OLLAMA_API_KEY",
            LlmProvider::Custom => "LLM_API_KEY",
        }
    }

    /// Whether the provider requires an authorization token.
    pub fn key_required(&self) -> bool {
        matches!(self.provider, LlmProvider::Openrouter | LlmProvider::Openai)
    }

    /// Authorization token, read from the environment via `lookup`.
    ///
    /// Values are trimmed and an empty string is treated as unset. Errors
    /// naming the environment variable when the provider requires one.
    pub fn read_key(
        &self,
        lookup: impl Fn(&str) -> Option<String>,
    ) -> anyhow::Result<Option<String>> {
        let name = self.api_key_env();
        let value = lookup(name).unwrap_or_default();
        let value = value.trim();
        if value.is_empty() {
            if self.key_required() {
                anyhow::bail!(
                    "{name} is required for provider '{}'",
                    self.provider.as_str()
                );
            }
            return Ok(None);
        }
        Ok(Some(value.to_owned()))
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

    fn llm_config(provider: &str, model: &str, base_url: Option<&str>) -> String {
        let mut raw = format!("[llm]\nprovider = \"{provider}\"\nmodel = \"{model}\"\n");
        if let Some(base_url) = base_url {
            raw.push_str(&format!("base_url = \"{base_url}\"\n"));
        }
        format!("{SAMPLE}\n{raw}")
    }

    #[test]
    fn parses_all_fields() {
        let config = Config::parse(SAMPLE).unwrap();
        assert_eq!(config.qdrant.url, "http://localhost:6334");
        assert_eq!(config.embedding.model, "bge-small-en-v1.5");
        assert_eq!(config.collection.active, "");
        assert!(config.reranker.is_none());
        assert!(config.server.is_none());
    }

    #[test]
    fn parses_reranker_section_with_defaults() {
        let raw = format!(
            "{SAMPLE}\n[reranker]\nbase_url = \"https://api.example.com/v1\"\nmodel = \"rerank-3\"\n"
        );
        let config = Config::parse(&raw).unwrap();
        let reranker = config.reranker.expect("reranker section");
        assert_eq!(reranker.base_url, "https://api.example.com/v1");
        assert_eq!(reranker.model, "rerank-3");
        assert_eq!(reranker.candidates, DEFAULT_RERANK_CANDIDATES);
        assert_eq!(reranker.timeout_ms, DEFAULT_RERANK_TIMEOUT_MS);
    }

    #[test]
    fn parses_reranker_overrides() {
        let raw = format!(
            "{SAMPLE}\n[reranker]\nbase_url = \"u\"\nmodel = \"m\"\ncandidates = 50\ntimeout_ms = 2500\n"
        );
        let reranker = Config::parse(&raw).unwrap().reranker.unwrap();
        assert_eq!(reranker.candidates, 50);
        assert_eq!(reranker.timeout_ms, 2500);
    }

    #[test]
    fn rejects_unknown_reranker_field() {
        let raw = format!("{SAMPLE}\n[reranker]\nbase_url = \"u\"\nmodel = \"m\"\nbogus = 1\n");
        assert!(Config::parse(&raw).is_err());
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
    fn corpus_defaults_when_absent() {
        let config = Config::parse(SAMPLE).unwrap();
        assert_eq!(config.corpus.prefix, "docs");
        assert_eq!(config.corpus.data_root, Path::new("data"));
    }

    #[test]
    fn parses_corpus_section_and_per_field_defaults() {
        let raw = format!("{SAMPLE}\n[corpus]\nprefix = \"eval-x\"\n");
        let config = Config::parse(&raw).unwrap();
        assert_eq!(config.corpus.prefix, "eval-x");
        assert_eq!(config.corpus.data_root, Path::new("data"));

        let raw = format!("{SAMPLE}\n[corpus]\ndata_root = \"data/x\"\n");
        let config = Config::parse(&raw).unwrap();
        assert_eq!(config.corpus.prefix, "docs");
        assert_eq!(config.corpus.data_root, Path::new("data/x"));
    }

    #[test]
    fn rejects_unknown_corpus_field() {
        let raw = format!("{SAMPLE}\n[corpus]\nbogus = 1\n");
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

    #[test]
    fn llm_defaults_per_provider() {
        for (provider, key_env, expected_base_url, required) in [
            (
                "openai",
                "OPENAI_API_KEY",
                "https://api.openai.com/v1",
                true,
            ),
            (
                "openrouter",
                "OPENROUTER_API_KEY",
                "https://openrouter.ai/api/v1",
                true,
            ),
            (
                "ollama",
                "OLLAMA_API_KEY",
                "http://localhost:11434/v1",
                false,
            ),
        ] {
            let config =
                Config::parse(&llm_config(provider, "m", None)).expect("expected to parse");
            let llm = config.llm.expect("llm section");
            assert_eq!(llm.provider.as_str(), provider);
            assert_eq!(llm.model, "m");
            assert_eq!(llm.base_url(), expected_base_url);
            assert_eq!(llm.api_key_env(), key_env);
            assert_eq!(llm.key_required(), required);
        }
    }

    #[test]
    fn llm_custom_defaults_to_its_own_base_url() {
        let config = Config::parse(&llm_config(
            "custom",
            "m",
            Some("https://api.example.com/openai/v1/"),
        ))
        .unwrap();
        let llm = config.llm.expect("llm section");
        assert_eq!(llm.base_url(), "https://api.example.com/openai/v1");
        assert_eq!(llm.api_key_env(), "LLM_API_KEY");
        assert!(!llm.key_required());
    }

    #[test]
    fn rejects_custom_without_base_url() {
        assert!(Config::parse(&llm_config("custom", "m", None)).is_err());
    }

    #[test]
    fn rejects_empty_model() {
        assert!(Config::parse(&llm_config("ollama", "", None)).is_err());
        assert!(Config::parse(&llm_config("ollama", "   ", None)).is_err());
    }

    #[test]
    fn rejects_unknown_provider() {
        assert!(Config::parse(&llm_config("azure", "m", None)).is_err());
    }

    #[test]
    fn rejects_unknown_llm_field() {
        let raw = format!("{}bogus = 1\n", llm_config("ollama", "m", None));
        assert!(Config::parse(&raw).is_err());
    }

    #[test]
    fn base_url_override_wins_for_builtin_providers() {
        for provider in ["openai", "openrouter", "ollama"] {
            let config = Config::parse(&llm_config(
                provider,
                "m",
                Some("http://proxy.example/v1//"),
            ))
            .unwrap();
            assert_eq!(
                config.llm.unwrap().base_url(),
                "http://proxy.example/v1",
                "provider={provider}"
            );
        }
    }

    #[test]
    fn rejects_empty_base_url() {
        assert!(Config::parse(&llm_config("openai", "m", Some(""))).is_err());
        assert!(Config::parse(&llm_config("openai", "m", Some("   "))).is_err());
    }

    #[test]
    fn rejects_missing_model() {
        let raw = format!("{SAMPLE}\n[llm]\nprovider = \"ollama\"\n");
        assert!(Config::parse(&raw).is_err());
    }

    #[test]
    fn llm_credential_helpers() {
        let required = Config::parse(&llm_config("openrouter", "m", None)).unwrap();
        let optional = Config::parse(&llm_config("ollama", "m", None)).unwrap();
        assert_eq!(
            required.llm.as_ref().unwrap().api_key_env(),
            "OPENROUTER_API_KEY"
        );
        assert!(required.llm.as_ref().unwrap().key_required());
        assert_eq!(
            optional.llm.as_ref().unwrap().api_key_env(),
            "OLLAMA_API_KEY"
        );
        assert!(!optional.llm.as_ref().unwrap().key_required());
    }

    #[test]
    fn read_key_required_and_optional() {
        let required = LlmConfig {
            provider: LlmProvider::Openai,
            model: "m".into(),
            base_url: None,
        };
        let optional = LlmConfig {
            provider: LlmProvider::Ollama,
            model: "m".into(),
            base_url: None,
        };

        // Required provider with the variable unset: an error naming it.
        let missing = required.read_key(|_| None).unwrap_err();
        assert!(
            missing.to_string().contains("OPENAI_API_KEY"),
            "error names the variable: {missing}"
        );

        // Optional provider with the variable unset or empty: no key, no error.
        assert_eq!(optional.read_key(|_| None).unwrap(), None);
        let empty = optional
            .read_key(|name| {
                if name == "OLLAMA_API_KEY" {
                    Some("".to_string())
                } else {
                    None
                }
            })
            .unwrap();
        assert_eq!(empty, None);

        let set = required
            .read_key(|name| {
                if name == "OPENAI_API_KEY" {
                    Some("  sk-secret  ".to_string())
                } else {
                    None
                }
            })
            .unwrap();
        assert_eq!(set, Some("sk-secret".to_owned()));
    }
}

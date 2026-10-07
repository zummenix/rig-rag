use std::path::{Path, PathBuf};

use anyhow::{Context, Result, bail};
use serde::Deserialize;

/// Path of the source list, relative to the working directory.
pub const SOURCES_PATH: &str = "sources.json";

/// A document source the ingest step fetches from.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Source {
    pub name: String,
    #[serde(rename = "type")]
    pub kind: SourceKind,
    pub url: String,
    #[serde(default = "default_ref")]
    pub r#ref: String,
    #[serde(default = "default_path")]
    pub path: String,
}

fn default_ref() -> String {
    "main".to_string()
}

fn default_path() -> String {
    ".".to_string()
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SourceKind {
    Git,
}

impl Source {
    /// Directory the source is fetched into, e.g. `data/jj`.
    pub fn dir(&self) -> PathBuf {
        Path::new("data").join(&self.name)
    }

    fn validate_name(&self) -> Result<()> {
        let valid = !self.name.is_empty()
            && self
                .name
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_');
        if !valid {
            bail!(
                "invalid source name {:?}: use ASCII letters, digits, '-' or '_'",
                self.name
            );
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(transparent)]
pub struct Sources(Vec<Source>);

impl Sources {
    pub fn load() -> Result<Self> {
        Self::from_path(SOURCES_PATH)
    }

    pub fn from_path(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        let raw = std::fs::read_to_string(path)
            .with_context(|| format!("failed to read sources at {}", path.display()))?;
        Self::parse(&raw)
    }

    pub fn parse(raw: &str) -> Result<Self> {
        let sources: Sources = serde_json::from_str(raw).context("failed to parse sources")?;
        for source in &sources.0 {
            source.validate_name()?;
        }
        Ok(sources)
    }

    pub fn iter(&self) -> impl Iterator<Item = &Source> {
        self.0.iter()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn applies_defaults() {
        let sources =
            Sources::parse(r#"[{"name":"jj","type":"git","url":"https://x/y"}]"#).unwrap();
        let source = sources.iter().next().unwrap();
        assert_eq!(source.r#ref, "main");
        assert_eq!(source.path, ".");
        assert_eq!(source.dir(), Path::new("data/jj"));
    }

    #[test]
    fn keeps_explicit_ref_and_path() {
        let sources = Sources::parse(
            r#"[{"name":"jj","type":"git","url":"https://x/y","ref":"v1","path":"docs"}]"#,
        )
        .unwrap();
        let source = sources.iter().next().unwrap();
        assert_eq!(source.r#ref, "v1");
        assert_eq!(source.path, "docs");
    }

    #[test]
    fn rejects_unknown_type() {
        assert!(Sources::parse(r#"[{"name":"x","type":"svn","url":"u"}]"#).is_err());
    }

    #[test]
    fn rejects_unknown_field() {
        assert!(Sources::parse(r#"[{"name":"x","type":"git","url":"u","extra":1}]"#).is_err());
    }

    #[test]
    fn rejects_unsafe_name() {
        assert!(Sources::parse(r#"[{"name":"../etc","type":"git","url":"u"}]"#).is_err());
    }
}

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
    pub fn dir(&self, data_root: impl AsRef<Path>) -> PathBuf {
        data_root.as_ref().join(&self.name)
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
    pub fn load(path: impl AsRef<Path>) -> Result<Self> {
        Self::from_path(path)
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

/// Rewrites the `ref` of each named source in the sources file at `path` to the
/// resolved commit SHA, leaving every other field untouched. Used by
/// `ingest --pin` so a corpus profile freezes to exact commits.
pub fn write_refs(path: impl AsRef<Path>, resolved: &[(String, String)]) -> Result<()> {
    let path = path.as_ref();
    let raw = std::fs::read_to_string(path)
        .with_context(|| format!("failed to read sources at {}", path.display()))?;
    let mut document: serde_json::Value =
        serde_json::from_str(&raw).context("failed to parse sources")?;
    let entries = document
        .as_array_mut()
        .context("sources file must be a JSON array")?;
    for entry in entries.iter_mut() {
        let Some(name) = entry.get("name").and_then(|name| name.as_str()) else {
            continue;
        };
        if let Some((_, sha)) = resolved.iter().find(|(n, _)| n == name) {
            entry["ref"] = serde_json::Value::String(sha.clone());
        }
    }
    let mut text =
        serde_json::to_string_pretty(&document).context("failed to serialize sources")?;
    text.push('\n');
    std::fs::write(path, text)
        .with_context(|| format!("failed to write sources at {}", path.display()))?;
    Ok(())
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
        assert_eq!(source.dir("data"), Path::new("data/jj"));
        assert_eq!(
            source.dir("data/single-project"),
            Path::new("data/single-project/jj")
        );
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

    #[test]
    fn write_refs_rewrites_only_named_sources() {
        let dir = std::env::temp_dir().join(format!("rig-rag-sources-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("sources.json");
        std::fs::write(
            &path,
            r#"[
  {"name":"jj","type":"git","url":"u","ref":"main","path":"docs"},
  {"name":"other","type":"git","url":"v","ref":"main"}
]
"#,
        )
        .unwrap();

        write_refs(&path, &[("jj".to_string(), "a".repeat(40))]).unwrap();

        let written = std::fs::read_to_string(&path).unwrap();
        let sources = Sources::parse(&written).unwrap();
        let jj = sources.iter().find(|s| s.name == "jj").unwrap();
        let other = sources.iter().find(|s| s.name == "other").unwrap();
        assert_eq!(jj.r#ref, "a".repeat(40));
        assert_eq!(jj.path, "docs");
        assert_eq!(other.r#ref, "main");
        let _ = std::fs::remove_dir_all(&dir);
    }
}

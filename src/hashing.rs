use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use sha2::{Digest, Sha256};

use crate::sources::Sources;

/// Hashes a source directory's contents: relative paths and file bytes,
/// independent of filesystem iteration order.
pub fn hash_source_dir(dir: impl AsRef<Path>) -> Result<String> {
    let dir = dir.as_ref();
    let mut files = Vec::new();
    collect_files(dir, dir, &mut files)?;
    files.sort();

    let mut hasher = Sha256::new();
    for rel in &files {
        let full = dir.join(rel);
        let bytes =
            fs::read(&full).with_context(|| format!("failed to read {}", full.display()))?;
        hasher.update(rel.to_string_lossy().as_bytes());
        hasher.update([0]);
        hasher.update((bytes.len() as u64).to_le_bytes());
        hasher.update(&bytes);
    }
    Ok(hex::encode(hasher.finalize()))
}

/// Per-source `(name, content hash)` pairs, in sources order.
pub fn hash_sources(
    sources: &Sources,
    data_root: impl AsRef<Path>,
) -> Result<Vec<(String, String)>> {
    let data_root = data_root.as_ref();
    sources
        .iter()
        .map(|source| {
            let hash = hash_source_dir(source.dir(data_root))
                .with_context(|| format!("failed to hash source {:?}", source.name))?;
            Ok((source.name.clone(), hash))
        })
        .collect()
}

/// Combines per-source hashes into one, independent of source order.
pub fn combine<'a>(hashes: impl IntoIterator<Item = (&'a str, &'a str)>) -> String {
    let mut pairs: Vec<(&str, &str)> = hashes.into_iter().collect();
    pairs.sort();

    let mut hasher = Sha256::new();
    for (name, hash) in pairs {
        hasher.update(name.as_bytes());
        hasher.update([0]);
        hasher.update(hash.as_bytes());
        hasher.update([0]);
    }
    hex::encode(hasher.finalize())
}

/// Collection name derived from the corpus prefix, model slug, and combined
/// hash, sanitized for Qdrant (which only accepts ASCII alphanumerics, `-` and
/// `_`). The production prefix is [`crate::config::DEFAULT_CORPUS_PREFIX`].
pub fn collection_name(prefix: &str, model_slug: &str, combined: &str) -> String {
    format!(
        "{}-{}-{}",
        sanitize(prefix),
        sanitize(model_slug),
        &combined[..12.min(combined.len())]
    )
}

/// Recovers the (sanitized) model slug from a collection name built by
/// [`collection_name`], e.g. `docs-bge-small-en-v1-5-0123456789ab` with prefix
/// `docs` -> `bge-small-en-v1-5`.
pub fn model_of<'a>(prefix: &str, collection: &'a str) -> Option<&'a str> {
    let rest = collection
        .strip_prefix(&sanitize(prefix))?
        .strip_prefix('-')?;
    rest.rsplit_once('-').map(|(model, _hash)| model)
}

/// Replaces anything Qdrant rejects in a name with `-`.
pub fn sanitize(value: &str) -> String {
    value
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '-'
            }
        })
        .collect()
}

fn collect_files(root: &Path, dir: &Path, out: &mut Vec<PathBuf>) -> Result<()> {
    for entry in fs::read_dir(dir).with_context(|| format!("failed to read {}", dir.display()))? {
        let entry = entry?;
        let path = entry.path();
        let file_type = entry.file_type()?;
        if file_type.is_dir() {
            collect_files(root, &path, out)?;
        } else if file_type.is_file() {
            let rel = path.strip_prefix(root).expect("walked path is under root");
            out.push(rel.to_path_buf());
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicU64, Ordering};

    use super::*;

    static COUNTER: AtomicU64 = AtomicU64::new(0);

    struct TempDir(PathBuf);

    impl TempDir {
        fn new() -> Self {
            let n = COUNTER.fetch_add(1, Ordering::Relaxed);
            let dir = std::env::temp_dir().join(format!("rig-rag-hash-{}-{n}", std::process::id()));
            fs::create_dir_all(&dir).unwrap();
            TempDir(dir)
        }

        fn write(&self, rel: &str, contents: &str) {
            let path = self.0.join(rel);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, contents).unwrap();
        }

        fn path(&self) -> &Path {
            &self.0
        }
    }

    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn hash_is_stable_and_content_sensitive() {
        let dir = TempDir::new();
        dir.write("a.md", "hello");
        dir.write("nested/b.md", "world");
        let first = hash_source_dir(dir.path()).unwrap();
        assert_eq!(first, hash_source_dir(dir.path()).unwrap());

        dir.write("a.md", "hello!");
        assert_ne!(first, hash_source_dir(dir.path()).unwrap());
    }

    #[test]
    fn hash_changes_when_a_file_is_added() {
        let dir = TempDir::new();
        dir.write("a.md", "x");
        dir.write("b.md", "y");
        let before = hash_source_dir(dir.path()).unwrap();

        dir.write("c.md", "z");
        assert_ne!(before, hash_source_dir(dir.path()).unwrap());
    }

    #[test]
    fn combine_is_order_independent() {
        assert_eq!(
            combine([("jj", "h1"), ("other", "h2")]),
            combine([("other", "h2"), ("jj", "h1")])
        );
    }

    #[test]
    fn collection_name_is_qdrant_safe() {
        assert_eq!(
            collection_name("docs", "bge-small-en-v1.5", "0123456789abcdef"),
            "docs-bge-small-en-v1-5-0123456789ab"
        );
    }

    #[test]
    fn collection_name_uses_custom_prefix() {
        assert_eq!(
            collection_name(
                "eval-single-project",
                "bge-small-en-v1.5",
                "0123456789abcdef"
            ),
            "eval-single-project-bge-small-en-v1-5-0123456789ab"
        );
    }

    #[test]
    fn model_of_round_trips_collection_name() {
        let name = collection_name("docs", "bge-small-en-v1.5", "0123456789abcdef");
        assert_eq!(model_of("docs", &name), Some("bge-small-en-v1-5"));
        assert_eq!(model_of("other", &name), None);
        assert_eq!(model_of("docs", "unrelated"), None);

        let name = collection_name("eval-multi-project", "bge-small-en-v1.5", "0123456789ab");
        assert_eq!(
            model_of("eval-multi-project", &name),
            Some("bge-small-en-v1-5")
        );
    }
}

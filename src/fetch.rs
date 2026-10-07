use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::Command;

use anyhow::{Context, Result, bail};

use crate::sources::{Source, SourceKind, Sources};

/// Fetches every source into its `data/<name>` directory.
pub fn fetch_all(sources: &Sources) -> Result<()> {
    for source in sources.iter() {
        fetch(source).with_context(|| format!("failed to fetch source {:?}", source.name))?;
    }
    Ok(())
}

pub fn fetch(source: &Source) -> Result<()> {
    match source.kind {
        SourceKind::Git => fetch_git(source),
    }
}

fn fetch_git(source: &Source) -> Result<()> {
    let checkout = temp_checkout(&source.name);
    reset_dir(&checkout)?;

    let sparse = !source.path.is_empty() && source.path != ".";

    let mut args: Vec<OsString> = vec![
        "clone".into(),
        "--quiet".into(),
        "--depth".into(),
        "1".into(),
        "--branch".into(),
        source.r#ref.clone().into(),
    ];
    if sparse {
        args.push("--filter=blob:none".into());
        args.push("--sparse".into());
    }
    args.push(source.url.clone().into());
    args.push(checkout.clone().into_os_string());
    run_git(&args, &format!("clone {} ({})", source.name, source.url))?;

    if sparse {
        run_git(
            &[
                "-C".into(),
                checkout.clone().into_os_string(),
                "sparse-checkout".into(),
                "set".into(),
                source.path.clone().into(),
            ],
            &format!("sparse-checkout {} ({})", source.path, source.name),
        )?;
    }

    let fetched = if sparse {
        checkout.join(&source.path)
    } else {
        checkout.clone()
    };
    if !fetched.is_dir() {
        let _ = std::fs::remove_dir_all(&checkout);
        bail!(
            "path {:?} not found in {}@{}",
            source.path,
            source.url,
            source.r#ref
        );
    }

    let dest = source.dir();
    reset_dir(&dest)?;
    copy_dir(&fetched, &dest)?;
    let _ = std::fs::remove_dir_all(&checkout);
    Ok(())
}

fn run_git(args: &[OsString], what: &str) -> Result<()> {
    let status = Command::new("git")
        .args(args)
        .status()
        .context("failed to run git; is git installed and on PATH?")?;
    if !status.success() {
        bail!("git {what} failed with {status}");
    }
    Ok(())
}

fn temp_checkout(name: &str) -> PathBuf {
    std::env::temp_dir().join(format!("rig-rag-fetch-{}-{name}", std::process::id()))
}

fn reset_dir(path: &Path) -> Result<()> {
    if path.exists() {
        std::fs::remove_dir_all(path)
            .with_context(|| format!("failed to remove {}", path.display()))?;
    }
    std::fs::create_dir_all(path).with_context(|| format!("failed to create {}", path.display()))
}

fn copy_dir(from: &Path, to: &Path) -> Result<()> {
    for entry in
        std::fs::read_dir(from).with_context(|| format!("failed to read {}", from.display()))?
    {
        let entry = entry?;
        let file_type = entry.file_type()?;
        let target = to.join(entry.file_name());
        if file_type.is_dir() {
            std::fs::create_dir_all(&target)?;
            copy_dir(&entry.path(), &target)?;
        } else if file_type.is_file() {
            std::fs::copy(entry.path(), &target)
                .with_context(|| format!("failed to copy {}", entry.path().display()))?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn copy_dir_copies_nested_files() {
        let base = std::env::temp_dir().join(format!("rig-rag-fetch-test-{}", std::process::id()));
        let from = base.join("from");
        let to = base.join("to");
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(from.join("nested")).unwrap();
        std::fs::write(from.join("a.md"), "a").unwrap();
        std::fs::write(from.join("nested/b.md"), "b").unwrap();

        reset_dir(&to).unwrap();
        copy_dir(&from, &to).unwrap();

        assert_eq!(std::fs::read_to_string(to.join("a.md")).unwrap(), "a");
        assert_eq!(
            std::fs::read_to_string(to.join("nested/b.md")).unwrap(),
            "b"
        );
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn reset_dir_recreates_existing_directory() {
        let dir = std::env::temp_dir().join(format!("rig-rag-reset-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("stale.md"), "stale").unwrap();

        reset_dir(&dir).unwrap();

        assert!(dir.is_dir());
        assert!(!dir.join("stale.md").exists());
        let _ = std::fs::remove_dir_all(&dir);
    }
}

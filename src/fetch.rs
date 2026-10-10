use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::Command;

use anyhow::{Context, Result, bail};

use crate::sources::{Source, SourceKind, Sources};

/// A source resolved to the exact commit that was checked out.
pub struct Fetched {
    pub name: String,
    pub commit: String,
}

/// Fetches every source into `<data_root>/<name>`, returning the resolved commit
/// SHA for each (branches and tags resolve to the commit they point at).
pub fn fetch_all(sources: &Sources, data_root: impl AsRef<Path>) -> Result<Vec<Fetched>> {
    let data_root = data_root.as_ref();
    let mut fetched = Vec::new();
    for source in sources.iter() {
        println!("\nFetching source '{}'\n", source.name);
        let commit = fetch(source, data_root)
            .with_context(|| format!("failed to fetch source {:?}", source.name))?;
        println!("Resolved {} to {}", source.name, commit);
        fetched.push(Fetched {
            name: source.name.clone(),
            commit,
        });
    }
    Ok(fetched)
}

/// Fetches one source and returns the resolved commit SHA.
pub fn fetch(source: &Source, data_root: impl AsRef<Path>) -> Result<String> {
    match source.kind {
        SourceKind::Git => fetch_git(source, data_root),
    }
}

fn fetch_git(source: &Source, data_root: impl AsRef<Path>) -> Result<String> {
    let checkout = temp_checkout(&source.name);
    reset_dir(&checkout)?;

    let sparse = !source.path.is_empty() && source.path != ".";
    if is_full_sha(&source.r#ref) {
        fetch_git_sha(source, &checkout, sparse)?;
    } else {
        fetch_git_ref(source, &checkout, sparse)?;
    }

    let commit = rev_parse_head(&checkout)?;

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

    let dest = source.dir(data_root);
    reset_dir(&dest)?;
    copy_dir(&fetched, &dest)?;
    let _ = std::fs::remove_dir_all(&checkout);
    Ok(commit)
}

/// Clones a branch or tag. `git clone --branch` only accepts refs, so this path
/// is used whenever `ref` is not a raw commit SHA.
fn fetch_git_ref(source: &Source, checkout: &Path, sparse: bool) -> Result<()> {
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
    args.push(checkout.as_os_str().to_os_string());
    run_git(&args, &format!("clone {} ({})", source.name, source.url))?;

    if sparse {
        run_git(
            &[
                "-C".into(),
                checkout.as_os_str().to_os_string(),
                "sparse-checkout".into(),
                "set".into(),
                source.path.clone().into(),
            ],
            &format!("sparse-checkout {} ({})", source.path, source.name),
        )?;
    }
    Ok(())
}

/// Fetches a single commit by SHA into a detached checkout. A SHA is not a
/// branch or tag, so it needs an explicit init/fetch/checkout instead of
/// `git clone --branch`. Shallow and sparse settings are preserved.
fn fetch_git_sha(source: &Source, checkout: &Path, sparse: bool) -> Result<()> {
    let dir = checkout.as_os_str().to_os_string();

    run_git(
        &["init".into(), "--quiet".into(), dir.clone()],
        &format!("init ({})", source.name),
    )?;
    run_git(
        &[
            "-C".into(),
            dir.clone(),
            "remote".into(),
            "add".into(),
            "origin".into(),
            source.url.clone().into(),
        ],
        &format!("remote add ({})", source.name),
    )?;

    let mut fetch: Vec<OsString> = vec![
        "-C".into(),
        dir.clone(),
        "fetch".into(),
        "--quiet".into(),
        "--depth".into(),
        "1".into(),
    ];
    if sparse {
        fetch.push("--filter=blob:none".into());
    }
    fetch.push("origin".into());
    fetch.push(source.r#ref.clone().into());
    run_git(&fetch, &format!("fetch {} ({})", source.r#ref, source.name))?;

    if sparse {
        run_git(
            &[
                "-C".into(),
                dir.clone(),
                "sparse-checkout".into(),
                "set".into(),
                source.path.clone().into(),
            ],
            &format!("sparse-checkout {} ({})", source.path, source.name),
        )?;
    }

    run_git(
        &[
            "-C".into(),
            dir,
            "checkout".into(),
            "--quiet".into(),
            "--detach".into(),
            "FETCH_HEAD".into(),
        ],
        &format!("checkout {} ({})", source.r#ref, source.name),
    )
}

/// A `ref` names an exact commit when it is 40 hexadecimal digits; anything else
/// (branch, tag, short SHA) goes through the ref-based clone path.
fn is_full_sha(value: &str) -> bool {
    value.len() == 40 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn rev_parse_head(checkout: &Path) -> Result<String> {
    run_git_output(
        &[
            "-C".into(),
            checkout.as_os_str().to_os_string(),
            "rev-parse".into(),
            "HEAD".into(),
        ],
        "rev-parse HEAD",
    )
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

fn run_git_output(args: &[OsString], what: &str) -> Result<String> {
    let output = Command::new("git")
        .args(args)
        .output()
        .context("failed to run git; is git installed and on PATH?")?;
    if !output.status.success() {
        bail!("git {what} failed with {}", output.status);
    }
    Ok(String::from_utf8(output.stdout)
        .context("git output was not valid UTF-8")?
        .trim()
        .to_string())
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

    #[test]
    fn detects_full_sha_only() {
        assert!(is_full_sha(&"a".repeat(40)));
        assert!(is_full_sha("9a1ad09b16e87d5d5e25759e5027953f8257be98"));
        assert!(!is_full_sha("main"));
        assert!(!is_full_sha(&"a".repeat(39)));
        assert!(!is_full_sha(&"a".repeat(41)));
        assert!(!is_full_sha(&"g".repeat(40)));
    }

    #[test]
    fn fetch_git_checks_out_full_sha_detached() {
        if Command::new("git").arg("--version").output().is_err() {
            return;
        }

        let base = std::env::temp_dir().join(format!("rig-rag-sha-fetch-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let origin = base.join("origin");
        std::fs::create_dir_all(&origin).unwrap();

        let git = |args: &[&str]| {
            let status = Command::new("git")
                .arg("-C")
                .arg(&origin)
                .args(args)
                .status()
                .unwrap();
            assert!(status.success(), "git {args:?}");
        };

        git(&["init", "--quiet"]);
        git(&["config", "user.email", "eval@example.com"]);
        git(&["config", "user.name", "eval"]);
        // Commit A is the tip of branch `pinme`; commit B advances `main`, so the
        // SHA below is advertised but not the tip of the default branch.
        std::fs::write(origin.join("a.md"), "a").unwrap();
        git(&["add", "a.md"]);
        git(&["commit", "--quiet", "-m", "a"]);
        git(&["branch", "pinme"]);
        let pinned = rev_parse_head(&origin).unwrap();

        std::fs::write(origin.join("b.md"), "b").unwrap();
        git(&["add", "b.md"]);
        git(&["commit", "--quiet", "-m", "b"]);
        assert_ne!(pinned, rev_parse_head(&origin).unwrap());

        let source = Source {
            name: format!("sha{}", std::process::id()),
            kind: SourceKind::Git,
            // `file://` forces the smart transport, so shallow fetch is honored.
            url: format!("file://{}", origin.display()),
            r#ref: pinned.clone(),
            path: ".".to_string(),
        };
        let data_root = base.join("data");
        let commit = fetch(&source, &data_root).unwrap();

        assert_eq!(commit, pinned);
        assert_eq!(
            std::fs::read_to_string(data_root.join(&source.name).join("a.md")).unwrap(),
            "a"
        );
        assert!(!data_root.join(&source.name).join("b.md").exists());
        let _ = std::fs::remove_dir_all(&base);
    }
}

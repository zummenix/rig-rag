use std::path::Path;

use anyhow::{Result, bail};

use crate::config::Config;
use crate::{hashing, store};

/// Deletes every collection under the config's corpus prefix other than the
/// active one. Prints the plan instead of deleting unless `yes` is set.
pub async fn run(yes: bool, config_path: impl AsRef<Path>) -> Result<()> {
    let config = Config::load(&config_path)?;
    let client = store::client(&config.qdrant.url)?;
    let active = config.collection.active.clone();

    if active.is_empty() {
        bail!("no active collection configured ([collection].active is empty); refusing to prune");
    }

    let names = store::list_collection_names(&client).await?;
    let stale = stale_collections(&names, &active, &config.corpus.prefix);

    if stale.is_empty() {
        println!("Nothing to prune; only {active:?} exists under this corpus");
        return Ok(());
    }

    if !yes {
        println!("Would delete {} collection(s): {stale:?}", stale.len());
        println!("Re-run with --yes to delete them");
        return Ok(());
    }

    for name in &stale {
        store::delete_collection(&client, name).await?;
        println!("Deleted {name}");
    }
    println!("Kept {active:?}");
    Ok(())
}

/// Collections eligible for pruning: those sharing the config's `<prefix>-`
/// corpus, excluding the active one. Collections from other prefixes (another
/// profile, or production) are never touched.
fn stale_collections(names: &[String], active: &str, prefix: &str) -> Vec<String> {
    let prefix = format!("{}-", hashing::sanitize(prefix));
    names
        .iter()
        .filter(|name| name.as_str() != active && name.starts_with(&prefix))
        .cloned()
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn names(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn only_prunes_the_configs_prefix() {
        let all = names(&[
            "docs-a-111111111111",
            "docs-b-222222222222",
            "eval-single-project-c-333333333333",
            "eval-multi-project-d-444444444444",
        ]);
        assert_eq!(
            stale_collections(&all, "docs-a-111111111111", "docs"),
            names(&["docs-b-222222222222"])
        );
        assert_eq!(
            stale_collections(
                &all,
                "eval-single-project-c-333333333333",
                "eval-single-project"
            ),
            Vec::<String>::new()
        );
    }

    #[test]
    fn keeps_the_active_collection() {
        let all = names(&["docs-a-111111111111"]);
        assert!(stale_collections(&all, "docs-a-111111111111", "docs").is_empty());
    }
}

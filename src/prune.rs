use anyhow::{Result, bail};

use crate::config::Config;
use crate::store;

/// Deletes every collection other than the active one. Prints the plan instead
/// of deleting unless `yes` is set.
pub async fn run(yes: bool) -> Result<()> {
    let config = Config::load()?;
    let client = store::client(&config.qdrant.url)?;
    let active = config.collection.active.clone();

    if active.is_empty() {
        bail!("no active collection configured ([collection].active is empty); refusing to prune");
    }

    let stale: Vec<String> = store::list_collection_names(&client)
        .await?
        .into_iter()
        .filter(|name| name != &active)
        .collect();

    if stale.is_empty() {
        println!("Nothing to prune; only {active:?} exists.");
        return Ok(());
    }

    if !yes {
        println!("Would delete {} collection(s): {stale:?}", stale.len());
        println!("Re-run with --yes to delete them.");
        return Ok(());
    }

    for name in &stale {
        store::delete_collection(&client, name).await?;
        println!("Deleted {name}");
    }
    println!("Kept {active:?}.");
    Ok(())
}

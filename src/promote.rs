use std::path::Path;

use anyhow::{Result, bail};

use crate::config::{self, Config};
use crate::store;

/// Makes `collection` the active one by rewriting `[collection].active` in the
/// config at `config_path`. The next server start picks it up.
pub async fn run(collection: &str, config_path: impl AsRef<Path>) -> Result<()> {
    let config = Config::load(&config_path)?;
    let client = store::client(&config.qdrant.url)?;

    if !client.collection_exists(collection).await? {
        bail!("collection {collection:?} does not exist");
    }

    let previous = config.collection.active.clone();
    config::write_active(&config_path, collection)?;

    if previous.is_empty() || previous == collection {
        println!("Active collection set to {collection:?}.");
    } else {
        println!("Active collection {previous:?} -> {collection:?}.");
    }
    Ok(())
}

use std::path::PathBuf;

use anyhow::Result;
use clap::{Parser, Subcommand};

use rig_rag::{config, ingest, model, promote, prune, serve, sources};

#[derive(Parser)]
#[command(
    name = "rig-rag",
    about = "Retrieval-augmented generation over documents"
)]
struct Cli {
    /// Path to the runtime configuration file
    #[arg(long, global = true, default_value = config::CONFIG_PATH)]
    config: PathBuf,
    /// Path to the source list
    #[arg(long, global = true, default_value = sources::SOURCES_PATH)]
    sources: PathBuf,
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Fetch sources, embed them and insert into Qdrant
    Ingest {
        /// Rebuild even if the target collection already exists
        #[arg(long)]
        force: bool,
        /// Rewrite branch/tag refs in the sources file to resolved commit SHAs
        #[arg(long)]
        pin: bool,
    },
    /// Serve the retrieval and chat API over HTTP (and the built UI, if present)
    Serve {
        /// Address to bind, e.g. `127.0.0.1:8080`
        #[arg(long)]
        bind: Option<String>,
        /// Directory of built UI assets to serve at `/`
        #[arg(long)]
        site_dir: Option<PathBuf>,
    },
    /// Load the embedding model and print its identity
    Model,
    /// Set the active collection in the config file
    Promote {
        /// Collection name to promote (as printed by `ingest`)
        collection: String,
    },
    /// Delete collections under this config's corpus prefix, other than the active one
    Prune {
        /// Actually delete (otherwise the plan is printed only)
        #[arg(long)]
        yes: bool,
    },
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();
    let config = cli.config;
    let sources = cli.sources;

    match cli.command {
        Command::Ingest { force, pin } => ingest::run(force, pin, config, sources).await,
        Command::Serve { bind, site_dir } => {
            serve::run(serve::ServeArgs {
                bind,
                site_dir,
                config_path: config,
            })
            .await
        }
        Command::Model => model::run(config),
        Command::Promote { collection } => promote::run(&collection, config).await,
        Command::Prune { yes } => prune::run(yes, config).await,
    }
}

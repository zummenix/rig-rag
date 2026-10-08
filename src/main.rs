use std::path::PathBuf;

use anyhow::Result;
use clap::{Parser, Subcommand};

use rig_rag::{chat, ingest, model, promote, prune, query, serve};

#[derive(Parser)]
#[command(
    name = "rig-rag",
    about = "Retrieval-augmented generation over documents"
)]
struct Cli {
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
    },
    /// Retrieve the chunks most similar to a question
    Query {
        /// The question to search for
        #[arg(short, long)]
        question: String,
    },
    /// Launch the interactive RAG chatbot
    Chat,
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
    /// Set the active collection in rig-rag.toml
    Promote {
        /// Collection name to promote (as printed by `ingest`)
        collection: String,
    },
    /// Delete collections other than the active one
    Prune {
        /// Actually delete (otherwise the plan is printed only)
        #[arg(long)]
        yes: bool,
    },
}

#[tokio::main]
async fn main() -> Result<()> {
    match Cli::parse().command {
        Command::Ingest { force } => ingest::run(force).await,
        Command::Query { question } => query::run(&question).await,
        Command::Chat => chat::run().await,
        Command::Serve { bind, site_dir } => serve::run(serve::ServeArgs { bind, site_dir }).await,
        Command::Model => model::run(),
        Command::Promote { collection } => promote::run(&collection).await,
        Command::Prune { yes } => prune::run(yes).await,
    }
}

use anyhow::Result;
use clap::{Parser, Subcommand};

use rig_rag::{chat, ingest, model, promote, prune, query};

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
        Command::Model => model::run(),
        Command::Promote { collection } => promote::run(&collection).await,
        Command::Prune { yes } => prune::run(yes).await,
    }
}

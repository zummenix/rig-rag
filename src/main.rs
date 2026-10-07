use anyhow::Result;
use clap::{Parser, Subcommand};

use rig_rag::{chat, ingest, model, query};

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
    /// Embed local docs and insert them into Qdrant
    Ingest,
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
}

#[tokio::main]
async fn main() -> Result<()> {
    match Cli::parse().command {
        Command::Ingest => ingest::run().await,
        Command::Query { question } => query::run(&question).await,
        Command::Chat => chat::run().await,
        Command::Model => model::run(),
    }
}

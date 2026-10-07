use std::path::PathBuf;

use serde::{Deserialize, Serialize};

#[derive(Eq, PartialEq, Debug, Serialize, Deserialize, Clone)]
pub struct DocChunk {
    pub id: String,
    pub text: String,
}

impl rig::Embed for DocChunk {
    fn embed(
        &self,
        embedder: &mut rig::embeddings::TextEmbedder,
    ) -> std::prelude::v1::Result<(), rig::embeddings::EmbedError> {
        embedder.embed(self.text.clone());
        Ok(())
    }
}

pub fn chunk_md_doc((path, doc): (PathBuf, String)) -> Vec<DocChunk> {
    print!("Chunking '{}'", path.to_string_lossy());
    let chunks = chunkedrs::chunk(&doc)
        .markdown()
        .split()
        .into_iter()
        .map(|chunk| {
            let id = format!(
                "{}:[{}-{}]",
                path.to_string_lossy(),
                chunk.start_byte,
                chunk.end_byte
            );
            DocChunk {
                id,
                text: chunk.content,
            }
        })
        .collect::<Vec<_>>();
    println!(": {}", chunks.len());
    chunks
}

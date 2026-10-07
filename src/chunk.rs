use std::path::PathBuf;

use serde::{Deserialize, Serialize};

/// Represents document chunk - meta and embedding text.
///
/// IMPORTANT!
/// When changing layout the db should be also updated and all services restarted.
#[derive(Eq, PartialEq, Debug, Serialize, Deserialize, Clone)]
pub struct DocChunk {
    /// Document path including line number
    pub path: String,
    /// Text to embed
    pub text: String,
    /// Zero-based position in the chunk sequence
    pub index: usize,
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

/// Splits document into chunks.
pub fn chunk_md_doc((path, doc): &(PathBuf, String)) -> Vec<DocChunk> {
    let doc = Document::new(doc);
    let chunks = chunkedrs::chunk(doc.text)
        .markdown()
        .split()
        .into_iter()
        .map(|chunk| {
            let path = format!(
                "{}:{}",
                path.to_string_lossy(),
                doc.line_number(chunk.start_byte),
            );
            DocChunk {
                path,
                text: chunk.content,
                index: chunk.index,
            }
        })
        .collect::<Vec<_>>();
    println!("{} | {}", path.to_string_lossy(), chunks.len());
    chunks
}

struct Document<'a> {
    text: &'a str,
    line_starts: std::cell::OnceCell<Vec<usize>>,
}

impl<'a> Document<'a> {
    pub fn new(text: &'a str) -> Self {
        Self {
            text,
            line_starts: std::cell::OnceCell::new(),
        }
    }

    pub fn line_number(&self, byte_index: usize) -> usize {
        assert!(byte_index <= self.text.len());

        let line_starts = self.line_starts.get_or_init(|| {
            let mut starts = vec![0];

            for (i, byte) in self.text.bytes().enumerate() {
                if byte == b'\n' {
                    starts.push(i + 1);
                }
            }

            starts
        });

        // Number of line starts <= byte_index.
        line_starts.partition_point(|&start| start <= byte_index)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn line_number() {
        let doc = Document::new("hello\nworld\nfoo");

        assert_eq!(doc.line_number(0), 1);
        assert_eq!(doc.line_number(3), 1);
        assert_eq!(doc.line_number(7), 2);
        assert_eq!(doc.line_number(10), 2);
        assert_eq!(doc.line_number(13), 3);
    }
}

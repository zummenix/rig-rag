use std::path::PathBuf;

use chunkedrs::Chunk;
use serde::{Deserialize, Serialize};

/// Represents document chunk - meta and embedding text.
///
/// IMPORTANT!
/// When changing layout the db should be also updated and all services restarted.
#[derive(Eq, PartialEq, Debug, Serialize, Deserialize, Clone)]
pub struct DocChunk {
    /// Document path
    pub path: String,
    /// Start line of the text in a document
    pub start_line: usize,
    /// End line of the text in a document
    pub end_line: usize,
    /// Text to embed
    pub text: String,
    /// Zero-based position in the chunk sequence
    pub chunk_index: usize,
}

impl DocChunk {
    /// Source location of a chunk in a document including line numbers
    pub fn source_location(&self) -> String {
        let (path, start_line, end_line) = (&self.path, self.start_line, self.end_line);
        if start_line >= end_line {
            format!("{path}:{start_line}")
        } else {
            format!("{path}:{start_line}-{end_line}")
        }
    }
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
            let chunk_index = chunk.index;
            let start_line = doc.line_number(chunk.start_byte);
            let end_line = doc.line_number(chunk.end_byte);
            let text = enrich_chunk_content(chunk);
            DocChunk {
                path: path.to_string_lossy().to_string(),
                start_line,
                end_line,
                text,
                chunk_index,
            }
        })
        .collect::<Vec<_>>();
    println!("{} | {}", path.to_string_lossy(), chunks.len());
    chunks
}

fn enrich_chunk_content(chunk: Chunk) -> String {
    if chunk.section_path.is_empty() {
        chunk.content.trim().to_owned()
    } else {
        format!(
            "{}\n\n{}",
            prepare_section_path(chunk.section_path),
            chunk.content.trim()
        )
    }
}

fn prepare_section_path(mut sections: Vec<String>) -> String {
    let sections = if sections.len() > 3 {
        let first = sections.remove(0);
        let last = sections.pop().unwrap();
        let second_to_last = sections.pop().unwrap();
        vec![first, second_to_last, last]
    } else {
        sections
    };

    sections
        .iter()
        .map(|s| trim_section_leading_hash(s))
        .collect::<Vec<_>>()
        .join(" > ")
}

fn trim_section_leading_hash(section: &str) -> &str {
    section
        .trim_start_matches(|ch: char| ch.is_whitespace() || ch == '#')
        .trim_end_matches(char::is_whitespace)
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

    #[test]
    fn enrich_chunk_content_without_sections_trims_spaces() {
        let chunk = Chunk::new("\n  Access tokens expire after 60 minutes... \n");
        assert_eq!(
            enrich_chunk_content(chunk),
            String::from("Access tokens expire after 60 minutes...")
        );
    }

    #[test]
    fn enrich_chunk_content_with_sections_trims_spaces() {
        let chunk =
            Chunk::new("\nAccess tokens expire after 60 minutes...\n  ").with_section_path(vec![
                String::from("# FAQ   "),
                String::from("##  Auth "),
                String::from("###  OAuth token expiration  "),
            ]);
        assert_eq!(
            enrich_chunk_content(chunk),
            String::from(
                "FAQ > Auth > OAuth token expiration\n\nAccess tokens expire after 60 minutes..."
            )
        );
    }

    #[test]
    fn enrich_chunk_content_without_sections_does_nothing() {
        let chunk = Chunk::new("Access tokens expire after 60 minutes...");
        assert_eq!(
            enrich_chunk_content(chunk),
            String::from("Access tokens expire after 60 minutes...")
        );
    }

    #[test]
    fn enrich_chunk_content_with_three_sections_adds_them() {
        let chunk = Chunk::new("Access tokens expire after 60 minutes...").with_section_path(vec![
            String::from("# FAQ"),
            String::from("## Auth"),
            String::from("### OAuth token expiration"),
        ]);
        assert_eq!(
            enrich_chunk_content(chunk),
            String::from(
                "FAQ > Auth > OAuth token expiration\n\nAccess tokens expire after 60 minutes..."
            )
        );
    }

    #[test]
    fn enrich_chunk_content_with_many_sections_adds_first_and_two_last() {
        let chunk = Chunk::new("Access tokens expire after 60 minutes...").with_section_path(vec![
            String::from("# FAQ"),
            String::from("## Auth"),
            String::from("### OAuth"),
            String::from("#### Token expiration"),
        ]);
        assert_eq!(
            enrich_chunk_content(chunk),
            String::from(
                "FAQ > OAuth > Token expiration\n\nAccess tokens expire after 60 minutes..."
            )
        );
    }
}

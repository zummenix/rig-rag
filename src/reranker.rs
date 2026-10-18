use anyhow::{Context, Result};
use fastembed::{RerankInitOptions, RerankerModel, TextRerank};

/// Cross-encoder used to rerank fused retrieval candidates.
///
/// Jina v1 turbo is an English-only 37M-parameter model (~151 MB fp32 ONNX),
/// versus ~2.3 GB for BGE reranker v2-m3. That keeps `serve`'s memory
/// footprint bounded; the cost is a smaller, English-only model.
pub const MODEL: RerankerModel = RerankerModel::JINARerankerV1TurboEn;

/// The model id reported by fastembed, e.g. in startup diagnostics.
pub fn model_slug() -> String {
    MODEL.to_string()
}

/// Loads the reranking model, downloading its weights into the fastembed cache
/// on first use.
pub fn load() -> Result<TextRerank> {
    TextRerank::try_new(RerankInitOptions::new(MODEL).with_show_download_progress(true))
        .with_context(|| format!("failed to load reranking model {MODEL}"))
}

/// Scores each document against `question`, best first, returning `(index,
/// score)` pairs into `documents`.
pub fn rerank(
    model: &mut TextRerank,
    question: &str,
    documents: Vec<String>,
) -> Result<Vec<(usize, f32)>> {
    let documents: Vec<&str> = documents.iter().map(String::as_str).collect();
    let results = model
        .rerank(question, &documents, false, None)
        .context("failed to rerank documents")?;
    Ok(results
        .into_iter()
        .map(|result| (result.index, result.score))
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn selects_the_lightweight_english_reranker() {
        assert!(matches!(MODEL, RerankerModel::JINARerankerV1TurboEn));
        assert_eq!(model_slug(), "jinaai/jina-reranker-v1-turbo-en");
    }
}

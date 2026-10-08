use shared::{DocHit, Msg};

/// Number of most recent transcript messages forwarded to the model. The client
/// still owns the full transcript; this only bounds the LLM request.
pub const HISTORY_CAP: usize = 10;

/// System preamble for the RAG agent.
pub const PREAMBLE: &str = "You are a helpful assistant that answers questions about technical docs \
     using only the retrieved context.";

/// Keeps only the last [`HISTORY_CAP`] messages, preserving order.
pub fn cap_history(mut history: Vec<Msg>) -> Vec<Msg> {
    if history.len() <= HISTORY_CAP {
        history
    } else {
        history.split_off(history.len() - HISTORY_CAP)
    }
}

/// Renders retrieved hits as a context block, in retrieval order.
pub fn context_block(docs: &[DocHit]) -> String {
    let mut block = String::from("Retrieved context:");
    for (index, doc) in docs.iter().enumerate() {
        block.push_str(&format!(
            "\n\n[{}] {}\n{}",
            index + 1,
            doc.source_location(),
            doc.text
        ));
    }
    block
}

/// Builds the user turn: the question, prefixed by the retrieved context.
pub fn build_prompt(question: &str, docs: &[DocHit]) -> String {
    if docs.is_empty() {
        question.to_owned()
    } else {
        format!("{}\n\nQuestion: {question}", context_block(docs))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use shared::Role;

    fn msg(content: &str) -> Msg {
        Msg {
            role: Role::User,
            content: content.into(),
        }
    }

    fn hit(path: &str, text: &str) -> DocHit {
        DocHit {
            score: 0.9,
            path: path.into(),
            start_line: 1,
            end_line: 2,
            chunk_index: 0,
            text: text.into(),
        }
    }

    #[test]
    fn cap_history_keeps_last_n_in_order() {
        let history: Vec<_> = (0..25).map(|i| msg(&i.to_string())).collect();
        let capped = cap_history(history);
        assert_eq!(capped.len(), HISTORY_CAP);
        assert_eq!(capped.first().unwrap().content, "15");
        assert_eq!(capped.last().unwrap().content, "24");
    }

    #[test]
    fn cap_history_leaves_short_history_untouched() {
        let history = vec![msg("a"), msg("b")];
        assert_eq!(cap_history(history.clone()), history);
    }

    #[test]
    fn build_prompt_without_docs_is_the_question() {
        assert_eq!(build_prompt("why?", &[]), "why?");
    }

    #[test]
    fn build_prompt_contains_section_paths_and_text_in_order() {
        let docs = [hit("a.md", "first chunk"), hit("b.md", "second chunk")];
        let prompt = build_prompt("why?", &docs);

        assert!(prompt.contains("a.md:1-2"));
        assert!(prompt.contains("b.md:1-2"));
        assert!(prompt.contains("first chunk"));
        assert!(prompt.contains("second chunk"));
        assert!(prompt.contains("Question: why?"));

        let first = prompt.find("first chunk").unwrap();
        let second = prompt.find("second chunk").unwrap();
        assert!(first < second, "context must preserve retrieval order");
    }
}

# Experiment: widen the hybrid candidate pool (`hybrid_candidate_pool`)

**Hypothesis.** Hybrid retrieval fuses a BM25 sparse prefetch and a dense
prefetch with a 2:1 sparse weight and hands the reranker only the top 30 fused
candidates. RRF scores by rank, so a gold chunk that one retriever ranks deep
(dense rank 21–30, or sparse rank 21–29) is truncated before the reranker can
promote it. Fusing the same prefetches with Qdrant's default equal weights and
rescoring the top **60** fused candidates should let those deep golds reach the
reranker and lift mean `recall@7` on both profiles, with no answerable question
moving backward, at roughly twice the reranked documents per query. Falsified by
no recall movement, by any answerable question regressing, or by a rerank-cost
and latency increase that the quality gain does not justify.

**Status:** draft — hypothesis recorded; controlled change pending.

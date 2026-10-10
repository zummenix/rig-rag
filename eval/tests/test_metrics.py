import unittest

from eval.questions import Gold, Question
from eval.runner.metrics import delta, evaluate_at_k, mean, percentile


def make_hit(index, path, start=1, end=2, text="body"):
    return {
        "chunk_index": index,
        "path": path,
        "start_line": start,
        "end_line": end,
        "text": text,
        "score": 0.9 - index * 0.01,
    }


JJ_AUTHOR = Question(
    id="jj-author",
    question="Who wrote jj?",
    applies_to=("single-project", "multi-project"),
    gold=(Gold(source="jj", path="testimonials.md", must_contain="creator"),),
)


class EvaluateAtKTest(unittest.TestCase):
    def test_full_hit(self):
        hits = [
            make_hit(0, "data/single-project/jj/testimonials.md", text="the creator"),
            make_hit(1, "data/single-project/jj/other.md"),
        ]
        entry = evaluate_at_k(hits, JJ_AUTHOR, "data/single-project")
        self.assertEqual(entry["hit"], 1)
        self.assertEqual(entry["recall"], 1.0)
        self.assertEqual(entry["precision"], 0.5)
        self.assertAlmostEqual(entry["mrr"], 1.0)
        self.assertEqual(entry["purity"], 1.0)
        self.assertEqual(entry["hits"][0], "data/single-project/jj/testimonials.md:1-2")

    def test_miss_scores_zero_and_reports_locations(self):
        hits = [make_hit(0, "data/single-project/jj/unrelated.md")]
        entry = evaluate_at_k(hits, JJ_AUTHOR, "data/single-project")
        self.assertEqual(entry["hit"], 0)
        self.assertEqual(entry["recall"], 0.0)
        self.assertEqual(entry["precision"], 0.0)
        self.assertEqual(entry["mrr"], 0.0)

    def test_mrr_uses_first_relevant_rank(self):
        hits = [
            make_hit(0, "data/single-project/jj/a.md"),
            make_hit(1, "data/single-project/jj/testimonials.md", text="the creator"),
        ]
        entry = evaluate_at_k(hits, JJ_AUTHOR, "data/single-project")
        self.assertAlmostEqual(entry["mrr"], 0.5)

    def test_purity_counts_gold_source_hits(self):
        hits = [
            make_hit(0, "data/multi-project/jj/a.md"),
            make_hit(1, "data/multi-project/podman/b.md"),
        ]
        entry = evaluate_at_k(hits, JJ_AUTHOR, "data/multi-project")
        self.assertEqual(entry["purity"], 0.5)

    def test_partial_recall_over_multiple_gold(self):
        question = Question(
            id="two",
            question="?",
            applies_to=("multi-project",),
            gold=(Gold(source="jj", path="a.md"), Gold(source="jj", path="b.md")),
        )
        hits = [make_hit(0, "data/jj/a.md"), make_hit(1, "data/jj/c.md")]
        entry = evaluate_at_k(hits, question, "data")
        self.assertEqual(entry["recall"], 0.5)
        self.assertEqual(entry["hit"], 1)

    def test_unanswerable_reports_no_hit(self):
        question = Question(id="none", question="?", applies_to=("multi-project",), gold=())
        empty = evaluate_at_k([], question, "data")
        self.assertFalse(empty["answerable"])
        self.assertEqual(empty["no_hit"], 1)
        self.assertIsNone(empty["recall"])

        answered = evaluate_at_k([make_hit(0, "data/jj/a.md")], question, "data")
        self.assertEqual(answered["no_hit"], 0)

    def test_empty_hits_answerable(self):
        entry = evaluate_at_k([], JJ_AUTHOR, "data/single-project")
        self.assertEqual(entry["hit"], 0)
        self.assertEqual(entry["precision"], 0.0)
        self.assertEqual(entry["purity"], 0.0)


class PercentileTest(unittest.TestCase):
    def test_linear_interpolation(self):
        self.assertEqual(percentile([1, 2, 3, 4], 50), 2.5)
        self.assertAlmostEqual(percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95), 9.55)

    def test_single_and_empty(self):
        self.assertEqual(percentile([4.2], 95), 4.2)
        self.assertIsNone(percentile([], 50))

    def test_unsorted_input(self):
        self.assertEqual(percentile([4, 1, 3, 2], 50), 2.5)


class AggregationTest(unittest.TestCase):
    def test_mean_ignores_none(self):
        self.assertEqual(mean([1.0, None, 3.0]), 2.0)
        self.assertIsNone(mean([None, None]))
        self.assertIsNone(mean([]))

    def test_delta_requires_both(self):
        self.assertEqual(delta(0.5, 0.25), 0.25)
        self.assertIsNone(delta(None, 0.25))
        self.assertIsNone(delta(0.5, None))


if __name__ == "__main__":
    unittest.main()

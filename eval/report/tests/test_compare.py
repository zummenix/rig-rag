import unittest
from pathlib import Path

from eval.report.framework import Baseline, compare, load_results

FIXTURES = Path(__file__).parent / "fixtures"


class OrderedNamesTest(unittest.TestCase):
    def test_single_before_multi_then_alphabetical(self):
        self.assertEqual(
            compare.ordered_names(["multi-project", "single-project", "alpha"]),
            ["single-project", "multi-project", "alpha"],
        )


class DeltaTest(unittest.TestCase):
    def test_delta_and_percent(self):
        self.assertEqual(compare.delta(10, 4), -6)
        self.assertAlmostEqual(compare.percent(10, 4), -0.6)

    def test_missing_side_is_none(self):
        self.assertIsNone(compare.delta(None, 4))
        self.assertIsNone(compare.delta(4, None))

    def test_zero_base_has_no_percent(self):
        self.assertIsNone(compare.percent(0, 4))

    def test_non_numeric_is_none(self):
        self.assertIsNone(compare.delta("x", 4))


class IngestMetricsTest(unittest.TestCase):
    def test_none_document_is_empty(self):
        self.assertEqual(compare.ingest_metrics(None), {})

    def test_created_report_reads_nested_paths(self):
        document = {
            "schema_version": 1,
            "status": "created",
            "totals": {"embeddings": 5},
            "timing_ms": {"total": 100},
            "memory": {"peak_rss_bytes": 2048},
        }
        metrics = compare.ingest_metrics(document)
        self.assertEqual(metrics["embeddings"], 5)
        self.assertEqual(metrics["total"], 100)
        self.assertEqual(metrics["peak_rss"], 2048)

    def test_reused_report_has_no_measured_metrics(self):
        document = {
            "schema_version": 1,
            "status": "reused",
            "totals": {"embeddings": 0},
            "timing_ms": {"total": 0},
            "memory": {"peak_rss_bytes": 0},
        }
        metrics = compare.ingest_metrics(document)
        self.assertTrue(metrics)
        self.assertTrue(all(value is None for value in metrics.values()))


class RetrievalMetricsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = load_results(FIXTURES / "v2" / "results.json")

    def test_emits_every_metric_at_every_k(self):
        metrics = compare.retrieval_metrics(self.results, "single-project", (3, 7, 20))
        for k in (3, 7, 20):
            for metric in ("recall", "purity", "mrr"):
                self.assertIn(f"{metric}@{k}", metrics)

    def test_reads_timings_by_phase(self):
        metrics = compare.retrieval_metrics(self.results, "single-project", (3, 7, 20))
        self.assertAlmostEqual(metrics["search_p50_ms"], 8.1)
        self.assertAlmostEqual(metrics["search_p95_ms"], 8.37)
        self.assertAlmostEqual(metrics["rerank_p50_ms"], 910.0)
        self.assertAlmostEqual(metrics["p50_ms"], 10.35)  # e2e
        self.assertAlmostEqual(metrics["p95_ms"], 11.925)

    def test_question_metrics_are_keyed_per_k(self):
        questions = compare.question_metrics(self.results, "single-project", (3, 7, 20))
        author = questions["jj-author"]
        self.assertEqual(author["recall@3"], 0.0)
        self.assertEqual(author["recall@20"], 1.0)
        self.assertEqual(questions["jj-new-change"]["purity@20"], 1.0)


class CombinedProfilesTest(unittest.TestCase):
    def test_unions_candidate_and_baseline_parts(self):
        names = compare.combined_profiles(
            {"profiles": {"multi-project": {}}},
            ({"profile": "single-project"},),
            Baseline("baseline", results=None, ingest=({"profile": "extra"},)),
        )
        self.assertEqual(names, ["single-project", "multi-project", "extra"])


if __name__ == "__main__":
    unittest.main()

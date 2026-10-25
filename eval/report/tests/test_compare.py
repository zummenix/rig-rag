import copy
import unittest
from pathlib import Path

import eval.report.framework.loader as loader
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
        cls.v2 = load_results(FIXTURES / "v2" / "results.json")
        cls.v3 = load_results(FIXTURES / "v3" / "results.json")
        cls.v2_single = loader.series(cls.v2)[0]
        cls.v3_sparse = next(
            entry
            for entry in loader.series(cls.v3)
            if entry["profile"] == "single-project" and entry["mode"] == "sparse"
        )

    def test_emits_every_metric_at_every_k(self):
        for entry in (self.v2_single, self.v3_sparse):
            metrics = compare.retrieval_metrics(entry, (3, 7, 20))
            for k in (3, 7, 20):
                for metric in ("recall", "purity", "mrr"):
                    self.assertIn(f"{metric}@{k}", metrics)

    def test_reads_timings_by_phase(self):
        metrics = compare.retrieval_metrics(self.v2_single, (3, 7, 20))
        self.assertAlmostEqual(metrics["search_p50_ms"], 8.1)
        self.assertAlmostEqual(metrics["search_p95_ms"], 8.37)
        self.assertAlmostEqual(metrics["rerank_p50_ms"], 910.0)
        self.assertAlmostEqual(metrics["p50_ms"], 10.35)  # e2e
        self.assertAlmostEqual(metrics["p95_ms"], 11.925)

    def test_reads_a_v3_mode_entry(self):
        metrics = compare.retrieval_metrics(self.v3_sparse, (3, 7, 20))
        self.assertAlmostEqual(metrics["recall@20"], 1.0)
        self.assertAlmostEqual(metrics["search_p50_ms"], 5.0)
        self.assertAlmostEqual(metrics["mrr@20"], 1.0)

    def test_question_metrics_are_keyed_per_k(self):
        questions = compare.question_metrics(self.v2_single, (3, 7, 20))
        author = questions["jj-author"]
        self.assertEqual(author["recall@3"], 0.0)
        self.assertEqual(author["recall@20"], 1.0)
        self.assertEqual(questions["jj-new-change"]["purity@20"], 1.0)

        v3_questions = compare.question_metrics(self.v3_sparse, (3, 7, 20))
        self.assertEqual(v3_questions["jj-author"]["recall@7"], 1.0)
        self.assertEqual(v3_questions["jj-new-change"]["recall@7"], 1.0)


class PairSeriesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v2 = load_results(FIXTURES / "v2" / "results.json")
        cls.v3 = load_results(FIXTURES / "v3" / "results.json")

    def test_v3_pairs_every_profile_and_mode(self):
        pairs, only_candidate, only_baseline = compare.pair_series(self.v3, self.v3)
        self.assertEqual(len(pairs), 6)
        self.assertEqual(only_candidate, [])
        self.assertEqual(only_baseline, [])
        for profile, mode, candidate, baseline in pairs:
            self.assertEqual(candidate["mode"], mode)
            self.assertEqual(baseline["mode"], mode)

    def test_two_v2_documents_pair_as_unknown(self):
        pairs, only_candidate, only_baseline = compare.pair_series(self.v2, self.v2)
        self.assertEqual([(profile, mode) for profile, mode, _, _ in pairs], [
            ("single-project", "unknown"),
            ("multi-project", "unknown"),
        ])
        self.assertEqual(only_candidate, [])
        self.assertEqual(only_baseline, [])

    def test_v3_against_v2_leaves_every_mode_unpaired(self):
        pairs, only_candidate, only_baseline = compare.pair_series(self.v3, self.v2)
        self.assertEqual(pairs, [])
        self.assertEqual(
            only_candidate,
            [
                ("single-project", "hybrid"),
                ("single-project", "dense"),
                ("single-project", "sparse"),
                ("multi-project", "hybrid"),
                ("multi-project", "dense"),
                ("multi-project", "sparse"),
            ],
        )
        self.assertEqual(
            only_baseline,
            [("single-project", "unknown"), ("multi-project", "unknown")],
        )

    def test_a_subset_sweep_reports_the_unmatched_modes(self):
        subset = copy.deepcopy(self.v3)
        subset["modes"] = ["hybrid"]
        for profile in subset["profiles"].values():
            profile["modes"] = {"hybrid": profile["modes"]["hybrid"]}
            for question in profile["questions"]:
                question["modes"] = {"hybrid": question["modes"]["hybrid"]}
        subset["summary"]["single-project"]["modes"] = {
            "hybrid": subset["summary"]["single-project"]["modes"]["hybrid"]
        }
        subset["summary"]["multi-project"]["modes"] = {
            "hybrid": subset["summary"]["multi-project"]["modes"]["hybrid"]
        }
        subset["degradation"]["single-project->multi-project"]["modes"] = {
            "hybrid": subset["degradation"]["single-project->multi-project"]["modes"]["hybrid"]
        }

        pairs, only_candidate, only_baseline = compare.pair_series(subset, self.v3)
        self.assertEqual([(p, m) for p, m, _, _ in pairs], [
            ("single-project", "hybrid"),
            ("multi-project", "hybrid"),
        ])
        self.assertEqual(only_candidate, [])
        self.assertEqual(
            only_baseline,
            [
                ("single-project", "dense"),
                ("single-project", "sparse"),
                ("multi-project", "dense"),
                ("multi-project", "sparse"),
            ],
        )


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

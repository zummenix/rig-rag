import unittest

from eval.report.framework import Baseline, compare


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

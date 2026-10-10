import tempfile
import unittest
from pathlib import Path

from eval.report.framework import (
    ResultsError,
    UnsupportedSchemaVersion,
    latency_k,
    latest_results,
    load_results,
    ordered_profiles,
    swept_k_values,
    validate_results,
)

FIXTURES = Path(__file__).parent / "fixtures"


class LoadResultsTest(unittest.TestCase):
    def test_current_fixture_loads(self):
        results = load_results(FIXTURES / "v1" / "results.json")
        self.assertEqual(results["schema_version"], 1)
        self.assertEqual(results["experiment"], "baseline")

    def test_legacy_fixture_loads(self):
        results = load_results(FIXTURES / "v1_legacy" / "results.json")
        self.assertNotIn("k_values", results)
        self.assertNotIn("commit_ref", results)

    def test_missing_file(self):
        with self.assertRaises(ResultsError):
            load_results(FIXTURES / "does-not-exist.json")


class ValidateResultsTest(unittest.TestCase):
    def test_unsupported_newer_version_names_the_version(self):
        with self.assertRaises(UnsupportedSchemaVersion) as caught:
            validate_results({"schema_version": 2, "profiles": {}})
        message = str(caught.exception)
        self.assertIn("2", message)
        self.assertIn("newer than", message)

    def test_unsupported_older_version(self):
        with self.assertRaises(UnsupportedSchemaVersion) as caught:
            validate_results({"schema_version": 0, "profiles": {}})
        self.assertIn("older than", str(caught.exception))

    def test_missing_schema_version(self):
        with self.assertRaises(ResultsError):
            validate_results({"profiles": {}})

    def test_bool_is_not_a_valid_version(self):
        with self.assertRaises(ResultsError):
            validate_results({"schema_version": True, "profiles": {}})

    def test_non_object(self):
        with self.assertRaises(ResultsError):
            validate_results([1, 2, 3])

    def test_missing_profiles(self):
        with self.assertRaises(ResultsError):
            validate_results({"schema_version": 1})


class AccessorTest(unittest.TestCase):
    def setUp(self):
        self.current = load_results(FIXTURES / "v1" / "results.json")
        self.legacy = load_results(FIXTURES / "v1_legacy" / "results.json")

    def test_swept_k_is_recorded_explicitly(self):
        self.assertEqual(swept_k_values(self.current), (1, 7))

    def test_swept_k_is_derived_when_absent(self):
        self.assertEqual(swept_k_values(self.legacy), (1, 7))

    def test_swept_k_requires_something_to_derive_from(self):
        with self.assertRaises(ResultsError):
            swept_k_values({"profiles": {}})

    def test_latency_k_recorded(self):
        self.assertEqual(latency_k(self.current), 7)
        self.assertEqual(latency_k(self.legacy), 7)

    def test_latency_k_defaults_when_absent(self):
        self.assertEqual(latency_k({}), 7)

    def test_ordered_profiles_puts_single_first(self):
        self.assertEqual(
            ordered_profiles(self.current), ["single-project", "multi-project"]
        )

    def test_ordered_profiles_falls_back_to_alphabetical(self):
        self.assertEqual(ordered_profiles({"profiles": {"b": {}, "a": {}}}), ["a", "b"])


class LatestResultsTest(unittest.TestCase):
    def test_picks_the_newest_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for run in ("2026-01-01T00-00-00Z", "2026-02-01T00-00-00Z"):
                (root / run).mkdir()
                (root / run / "results.json").write_text("{}")
            self.assertEqual(
                latest_results(root), root / "2026-02-01T00-00-00Z" / "results.json"
            )

    def test_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ResultsError):
                latest_results(Path(tmp))


if __name__ == "__main__":
    unittest.main()

import copy
import tempfile
import unittest
from pathlib import Path

import eval.report.framework.loader as loader
from eval.report.framework import (
    ResultsError,
    UnsupportedSchemaVersion,
    latest_results,
    load_results,
    ordered_profiles,
    swept_k_values,
    validate_results,
)

FIXTURES = Path(__file__).parent / "fixtures"


class LoadResultsTest(unittest.TestCase):
    def test_current_fixture_loads(self):
        results = load_results(FIXTURES / "v3" / "results.json")
        self.assertEqual(results["schema_version"], 3)
        self.assertEqual(results["modes"], ["hybrid", "dense", "sparse"])
        self.assertEqual(results["experiment"], "baseline")

    def test_v3_profile_and_question_keys_are_exact(self):
        results = load_results(FIXTURES / "v3" / "results.json")
        profile = results["profiles"]["single-project"]
        self.assertEqual(
            list(profile),
            ["collection", "prefix", "data_root", "source_names", "modes", "questions"],
        )
        question = profile["questions"][0]
        self.assertEqual(
            list(question),
            ["id", "question", "answerable", "gold", "notes", "modes"],
        )
        for mode in results["modes"]:
            self.assertEqual(set(question["modes"][mode]), {"by_k", "timings", "rerank"})
            self.assertNotIn("hits", question["modes"][mode]["by_k"]["3"])
            self.assertIn("hits", question["modes"][mode]["by_k"]["20"])

    def test_v2_fixture_still_loads(self):
        # v2 predates `mode`; it remains renderable for existing artifacts.
        results = load_results(FIXTURES / "v2" / "results.json")
        self.assertEqual(results["schema_version"], 2)
        self.assertEqual(results["experiment"], "baseline")

    def test_missing_file(self):
        with self.assertRaises(ResultsError):
            load_results(FIXTURES / "does-not-exist.json")


class ValidateResultsTest(unittest.TestCase):
    def setUp(self):
        self.v3 = load_results(FIXTURES / "v3" / "results.json")

    def test_v1_is_refused_with_the_comparability_reason(self):
        with self.assertRaises(UnsupportedSchemaVersion) as caught:
            validate_results({"schema_version": 1, "profiles": {}})
        message = str(caught.exception)
        self.assertIn("v1", message)
        self.assertIn("not comparable", message)

    def test_unsupported_newer_version_names_the_version(self):
        with self.assertRaises(UnsupportedSchemaVersion) as caught:
            validate_results({"schema_version": 4, "profiles": {}})
        message = str(caught.exception)
        self.assertIn("4", message)
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
            validate_results({"schema_version": 2})

    def test_v3_requires_a_non_empty_modes_list(self):
        for value in (None, [], ["hybrid", "hybrid"]):
            document = copy.deepcopy(self.v3)
            if value is None:
                document.pop("modes", None)
            else:
                document["modes"] = value
            with self.assertRaises(ResultsError):
                validate_results(document)

    def test_v3_rejects_an_unknown_mode(self):
        document = copy.deepcopy(self.v3)
        document["modes"] = ["hybrid", "dense", "fuzzy"]
        with self.assertRaises(ResultsError):
            validate_results(document)

    def test_v3_requires_matching_profile_modes(self):
        document = copy.deepcopy(self.v3)
        document["profiles"]["single-project"]["modes"].pop("dense")
        with self.assertRaises(ResultsError):
            validate_results(document)

    def test_v3_requires_matching_question_modes(self):
        document = copy.deepcopy(self.v3)
        document["profiles"]["single-project"]["questions"][0]["modes"].pop("sparse")
        with self.assertRaises(ResultsError):
            validate_results(document)


class RecordedModesTest(unittest.TestCase):
    def test_v3_records_its_modes(self):
        results = load_results(FIXTURES / "v3" / "results.json")
        self.assertEqual(loader.recorded_modes(results), ("hybrid", "dense", "sparse"))

    def test_v2_has_no_recorded_mode(self):
        results = load_results(FIXTURES / "v2" / "results.json")
        self.assertIsNone(loader.recorded_modes(results))


class SeriesTest(unittest.TestCase):
    def test_v3_yields_one_entry_per_profile_and_mode(self):
        results = load_results(FIXTURES / "v3" / "results.json")
        series = loader.series(results)

        self.assertEqual(len(series), 6)
        self.assertEqual(
            [(entry["profile"], entry["mode"]) for entry in series],
            [
                ("single-project", "hybrid"),
                ("single-project", "dense"),
                ("single-project", "sparse"),
                ("multi-project", "hybrid"),
                ("multi-project", "dense"),
                ("multi-project", "sparse"),
            ],
        )
        first = series[0]
        self.assertEqual(first["summary"], results["summary"]["single-project"]["modes"]["hybrid"])
        self.assertEqual(first["counts"]["questions_applied"], 2)
        self.assertEqual(first["timings"], results["profiles"]["single-project"]["modes"]["hybrid"]["timings"])
        self.assertEqual(first["rerank"], results["profiles"]["single-project"]["modes"]["hybrid"]["rerank"])
        self.assertEqual(len(first["questions"]), 2)
        self.assertIn("by_k", first["questions"][0])
        self.assertNotIn("modes", first["questions"][0])

    def test_v2_yields_one_unknown_entry_per_profile(self):
        results = load_results(FIXTURES / "v2" / "results.json")
        series = loader.series(results)

        self.assertEqual(len(series), 2)
        for entry in series:
            self.assertEqual(entry["mode"], "unknown")
            self.assertEqual(entry["label"], loader.UNKNOWN_MODE_LABEL)
            self.assertEqual(len(entry["questions"]), 2)
        self.assertEqual(
            series[0]["summary"], results["summary"]["single-project"]
        )


class DegradationModesTest(unittest.TestCase):
    def test_v3_keeps_the_mode_map(self):
        results = load_results(FIXTURES / "v3" / "results.json")
        entry = next(iter(loader.degradation_modes(results).values()))
        self.assertEqual(set(entry["modes"]), {"hybrid", "dense", "sparse"})
        self.assertEqual(entry["questions_compared"], ["jj-new-change"])

    def test_v2_wraps_the_flat_entry_as_unknown(self):
        results = load_results(FIXTURES / "v2" / "results.json")
        entry = next(iter(loader.degradation_modes(results).values()))
        self.assertEqual(set(entry["modes"]), {"unknown"})
        self.assertIn("recall@3", entry["modes"]["unknown"])
        self.assertNotIn("from", entry["modes"]["unknown"])


class AccessorTest(unittest.TestCase):
    def setUp(self):
        self.current = load_results(FIXTURES / "v2" / "results.json")

    def test_swept_k_is_recorded_explicitly(self):
        self.assertEqual(swept_k_values(self.current), (3, 7, 20))

    def test_swept_k_requires_a_recorded_list(self):
        with self.assertRaises(ResultsError):
            swept_k_values({"profiles": {}})
        with self.assertRaises(ResultsError):
            swept_k_values({"profiles": {}, "k_values": []})

    def test_ordered_profiles_puts_single_first(self):
        self.assertEqual(
            ordered_profiles(self.current), ["single-project", "multi-project"]
        )

    def test_ordered_profiles_falls_back_to_alphabetical(self):
        self.assertEqual(ordered_profiles({"profiles": {"b": {}, "a": {}}}), ["a", "b"])

    def test_profile_rank_puts_known_profiles_first(self):
        self.assertEqual(loader.profile_rank("single-project"), 0)
        self.assertEqual(loader.profile_rank("multi-project"), 1)
        self.assertEqual(loader.profile_rank("zeta"), 2)

    def test_latency_k_accessor_is_gone(self):
        self.assertFalse(hasattr(loader, "latency_k"))
        self.assertFalse(hasattr(loader, "DEFAULT_LATENCY_K"))


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

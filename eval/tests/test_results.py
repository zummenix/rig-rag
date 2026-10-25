import json
import tempfile
import unittest
from pathlib import Path

from eval.questions import Gold, Question
from eval.runner import results as results_mod
from eval.runner.metrics import K_VALUES

MODES = results_mod.SEARCH_MODES


def answered_entry(k, value):
    return {
        "answerable": True,
        "hits_returned": 1,
        "no_hit": None,
        "hit": 1,
        "recall": value,
        "precision": 1.0,
        "mrr": value,
        "purity": value,
    }


def unanswered_entry():
    return {
        "answerable": False,
        "hits_returned": 0,
        "no_hit": 1,
        "hit": None,
        "recall": None,
        "precision": None,
        "mrr": None,
        "purity": None,
    }


def timing(samples):
    return {
        "samples_ms": samples,
        "p50_ms": float(samples[0]),
        "p95_ms": float(samples[-1]),
        "mean_ms": sum(samples) / len(samples),
        "sum_ms": sum(samples),
    }


def make_question(question_id, answerable=True):
    gold = (Gold(source="jj", path="testimonials.md", must_contain="creator"),) if answerable else ()
    return Question(
        id=question_id,
        question=f"question {question_id}",
        applies_to=("single-project", "multi-project"),
        gold=gold,
        notes="n",
    )


def profile_result(
    collection,
    keys,
    question_ids,
    scale=1.0,
    e2e_p95=2.0,
    modes=MODES,
):
    questions = []
    for question_id in question_ids:
        answerable = question_id != "neg"
        question = make_question(question_id, answerable=answerable)
        by_mode = {}
        for mode in modes:
            if answerable:
                by_k = {str(k): answered_entry(k, scale * k / 20.0) for k in keys}
            else:
                by_k = {str(k): unanswered_entry() for k in keys}
            by_mode[mode] = {
                "by_k": by_k,
                "timings": {"e2e_ms": e2e_p95, "search_ms": 10.0, "rerank_ms": 100.0},
                "rerank": {
                    "docs": 30,
                    "results": 30,
                    "tokens": 39,
                    "cost": 0.000351,
                    "cost_rub": 0.03,
                    "status": "success",
                },
            }
        questions.append(
            {
                "id": question_id,
                "question": question.question,
                "answerable": question.answerable,
                "gold": results_mod.gold_entry(question),
                "notes": question.notes,
                "modes": by_mode,
            }
        )
    return {
        "collection": collection,
        "prefix": "eval-x",
        "data_root": f"data/{collection}",
        "source_names": ["jj"],
        "modes": {
            mode: {
                "timings": {
                    "search": timing([10.0, 20.0]),
                    "rerank": timing([100.0, 200.0]),
                    "e2e": timing([1.0, e2e_p95]),
                },
                "rerank": {
                    "calls": 2,
                    "documents": 60,
                    "tokens": 80,
                    "cost": 0.0007,
                    "cost_rub": 0.06,
                    "p50_ms": 100.0,
                    "p95_ms": 200.0,
                    "failures": 0,
                },
            }
            for mode in modes
        },
        "questions": questions,
    }


def build(
    keys,
    single_ids=("a", "b"),
    multi_ids=("a", "b", "neg"),
    multi_scale=0.0,
    modes=MODES,
):
    profiles = {
        "single-project": profile_result(
            "eval-single", keys, single_ids, 1.0, e2e_p95=2.0, modes=modes
        ),
        "multi-project": profile_result(
            "eval-multi", keys, multi_ids, multi_scale, e2e_p95=4.0, modes=modes
        ),
    }
    return results_mod.build_results(
        experiment="baseline",
        run="2026-10-10T12-00-00Z",
        started_at="2026-10-10T12:00:00Z",
        commit="deadbeef",
        commit_ref="origin/main",
        config={"single-project": {"rig-rag.toml": {}, "sources.json": []}},
        environment={"os": "darwin"},
        profiles=profiles,
        modes=modes,
        k_values=keys,
    )


class BuildResultsTest(unittest.TestCase):
    def setUp(self):
        self.results = build(K_VALUES)

    def test_top_level_contract(self):
        self.assertEqual(self.results["schema_version"], 3)
        for key in (
            "experiment",
            "run",
            "started_at",
            "commit",
            "commit_ref",
            "config",
            "environment",
            "modes",
            "k_values",
            "profiles",
            "degradation",
            "summary",
        ):
            self.assertIn(key, self.results)
        self.assertEqual(self.results["modes"], list(MODES))
        self.assertNotIn("mode", self.results)
        self.assertEqual(self.results["k_values"], list(K_VALUES))
        self.assertEqual(self.results["commit_ref"], "origin/main")

    def test_every_profile_and_question_records_every_mode(self):
        for profile in self.results["profiles"].values():
            self.assertEqual(set(profile["modes"]), set(MODES))
            for question in profile["questions"]:
                self.assertEqual(set(question["modes"]), set(MODES))
                for mode in MODES:
                    self.assertIn("by_k", question["modes"][mode])
                    self.assertIn("timings", question["modes"][mode])
                    self.assertIn("rerank", question["modes"][mode])

    def test_question_metadata_is_stored_once(self):
        question = self.results["profiles"]["single-project"]["questions"][0]
        self.assertNotIn("by_k", question)
        self.assertNotIn("timings", question)
        self.assertNotIn("mode", question)

    def test_modes_are_canonicalized(self):
        results = build(K_VALUES, modes=("sparse", "hybrid"))
        self.assertEqual(results["modes"], ["hybrid", "sparse"])
        for profile in results["profiles"].values():
            self.assertEqual(set(profile["modes"]), {"hybrid", "sparse"})

    def test_modes_are_validated(self):
        with self.assertRaises(ValueError):
            build(K_VALUES, modes=("fuzzy",))
        with self.assertRaises(ValueError):
            build(K_VALUES, modes=())
        with self.assertRaises(ValueError):
            build(K_VALUES, modes=("hybrid", "hybrid"))

    def test_v1_keys_are_removed(self):
        profile = self.results["profiles"]["single-project"]
        self.assertNotIn("latency", profile)
        question = profile["questions"][0]
        for key in ("latency_ms", "cold_ms"):
            self.assertNotIn(key, question)

    def test_degradation_covers_every_metric_and_mode(self):
        entry = self.results["degradation"]["single-project->multi-project"]
        self.assertEqual(entry["questions_compared"], ["a", "b"])
        self.assertEqual(set(entry["modes"]), set(MODES))
        for mode in MODES:
            metrics = entry["modes"][mode]
            for metric in ("recall", "purity", "mrr"):
                for k in K_VALUES:
                    self.assertIn(f"{metric}@{k}", metrics)
            # single recall@7 = 7/20; multi returns nothing (scale 0).
            self.assertAlmostEqual(metrics["recall@7"], -0.35)
            self.assertAlmostEqual(metrics["purity@7"], -0.35)
            self.assertAlmostEqual(metrics["mrr@20"], -1.0)
            # e2e p95: 4.0 - 2.0.
            self.assertAlmostEqual(metrics["p95_ms"], 2.0)

    def test_summary_counts_once_and_metrics_per_mode(self):
        single = self.results["summary"]["single-project"]
        self.assertEqual(single["answerable"], 2)
        self.assertEqual(single["unanswerable"], 0)
        self.assertNotIn("mean_recall", single)
        self.assertEqual(set(single["modes"]), set(MODES))
        for mode in MODES:
            entry = single["modes"][mode]
            self.assertIsNone(entry["unanswerable_no_hit_rate"])
            for k in K_VALUES:
                self.assertAlmostEqual(entry["mean_recall"][f"recall@{k}"], k / 20.0)
                self.assertAlmostEqual(entry[f"mean_purity@{k}"], k / 20.0)
                self.assertAlmostEqual(entry[f"mean_mrr@{k}"], k / 20.0)

        multi = self.results["summary"]["multi-project"]
        self.assertEqual(multi["answerable"], 2)
        self.assertEqual(multi["unanswerable"], 1)
        for mode in MODES:
            self.assertEqual(multi["modes"][mode]["unanswerable_no_hit_rate"], 1.0)
            self.assertEqual(multi["modes"][mode]["mean_recall"]["recall@7"], 0.0)

    def test_summary_timing_rollup_per_mode(self):
        timings = self.results["summary"]["single-project"]["modes"]["dense"]["timings"]
        self.assertEqual(timings["search"]["p50_ms"], 10.0)
        self.assertEqual(timings["rerank"]["p95_ms"], 200.0)
        self.assertEqual(timings["e2e"]["p95_ms"], 2.0)
        self.assertEqual(timings["e2e"]["mean_ms"], 1.5)

    def test_rerank_rollup_is_per_mode(self):
        for mode in MODES:
            rerank = self.results["summary"]["single-project"]["modes"][mode]["rerank"]
            self.assertEqual(rerank["calls"], 2)
            self.assertEqual(rerank["documents"], 60)
            self.assertEqual(rerank["failures"], 0)


class SubsetSweepTest(unittest.TestCase):
    """Regression: a subset sweep must not KeyError in aggregation."""

    def test_build_results_with_single_k(self):
        results = build((3,))
        self.assertEqual(results["k_values"], [3])

        single = results["summary"]["single-project"]["modes"]["hybrid"]
        self.assertEqual(list(single["mean_recall"]), ["recall@3"])
        self.assertIn("mean_purity@3", single)
        self.assertIn("mean_mrr@3", single)

        entry = results["degradation"]["single-project->multi-project"]["modes"]["hybrid"]
        self.assertIn("recall@3", entry)
        self.assertIn("purity@3", entry)
        self.assertNotIn("recall@7", entry)

    def test_build_results_with_multi_k_subset(self):
        results = build((3, 20))
        self.assertEqual(results["k_values"], [3, 20])
        self.assertEqual(
            list(results["summary"]["single-project"]["modes"]["sparse"]["mean_recall"]),
            ["recall@3", "recall@20"],
        )


class ValidateMetricKTest(unittest.TestCase):
    def test_normalizes_sorts_dedupes(self):
        self.assertEqual(results_mod.validate_metric_k([20, 3, 7, 3]), (3, 7, 20))

    def test_empty_sweep_rejected(self):
        with self.assertRaises(ValueError):
            results_mod.validate_metric_k(())


class ValidateModesTest(unittest.TestCase):
    def test_canonical_order(self):
        self.assertEqual(
            results_mod.validate_modes(("sparse", "hybrid")), ("hybrid", "sparse")
        )

    def test_rejects_unknown_empty_and_duplicate(self):
        for modes in ((), ("fuzzy",), ("dense", "dense")):
            with self.assertRaises(ValueError):
                results_mod.validate_modes(modes)


class WriteResultsTest(unittest.TestCase):
    def test_writes_pretty_json_and_hits(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "nested" / "run"
            path = results_mod.write_results(run_dir, {"schema_version": 2})
            self.assertTrue(path.is_file())
            self.assertTrue(path.read_text().endswith("\n"))
            self.assertEqual(json.loads(path.read_text())["schema_version"], 2)

            hits_path = results_mod.write_hits(
                run_dir, [{"question": "a"}, {"question": "b"}]
            )
            lines = hits_path.read_text().splitlines()
            self.assertEqual([json.loads(line)["question"] for line in lines], ["a", "b"])


class ConfigSnapshotTest(unittest.TestCase):
    def test_gold_entry_omits_absent_fields(self):
        question = Question(
            id="q",
            question="?",
            applies_to=("multi-project",),
            gold=(Gold(source="jj", path="a.md"), Gold(source="jj", path="b.md", lines=(1, 2), must_contain="x")),
        )
        self.assertEqual(
            results_mod.gold_entry(question),
            [
                {"source": "jj", "path": "a.md"},
                {"source": "jj", "path": "b.md", "lines": [1, 2], "must_contain": "x"},
            ],
        )


if __name__ == "__main__":
    unittest.main()

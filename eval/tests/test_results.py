import json
import tempfile
import unittest
from pathlib import Path

from eval.questions import Gold, Question
from eval.runner import results as results_mod
from eval.runner.metrics import K_VALUES


def answered_entry(k, scale=1.0):
    """A per-k entry whose value depends on `k`, so reading the wrong k shows."""

    value = scale * k / 20.0
    return {
        "answerable": True,
        "hits_returned": 1,
        "no_hit": None,
        "hit": 1,
        "recall": value,
        "precision": 1.0,
        "mrr": value,
        "purity": value,
        "hits": [],
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
        "hits": [],
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


def profile_result(collection, keys, question_ids, scale=1.0, latency_k=7):
    questions = []
    for question_id in question_ids:
        answerable = question_id != "neg"
        question = make_question(question_id, answerable=answerable)
        if answerable:
            by_k = {str(k): answered_entry(k, scale) for k in keys}
        else:
            by_k = {str(k): unanswered_entry() for k in keys}
        questions.append(
            {
                "id": question_id,
                "question": question.question,
                "answerable": question.answerable,
                "gold": results_mod.gold_entry(question),
                "notes": question.notes,
                "by_k": by_k,
                "latency_ms": [1.0, 2.0],
            }
        )
    return {
        "collection": collection,
        "prefix": "eval-x",
        "data_root": f"data/{collection}",
        "source_names": ["jj"],
        "latency": {
            "warmup": 3,
            "reps": 2,
            "latency_k": latency_k,
            "samples_ms": [1.0, 2.0],
            "p50_ms": 1.5,
            "p95_ms": 2.0,
        },
        "questions": questions,
    }


def build(keys, latency_k=7, single_ids=("a", "b"), multi_ids=("a", "b", "neg"), multi_scale=0.0):
    profiles = {
        "single-project": profile_result("eval-single", keys, single_ids, 1.0, latency_k),
        "multi-project": profile_result("eval-multi", keys, multi_ids, multi_scale, latency_k),
    }
    return results_mod.build_results(
        experiment="baseline",
        run="2026-10-10T12-00-00Z",
        started_at="2026-10-10T12:00:00Z",
        commit="deadbeef",
        config={"single-project": {"rig-rag.toml": {}, "sources.json": []}},
        environment={"os": "darwin"},
        profiles=profiles,
        k_values=keys,
        latency_k=latency_k,
    )


class BuildResultsTest(unittest.TestCase):
    def setUp(self):
        self.results = build(K_VALUES)

    def test_top_level_contract(self):
        self.assertEqual(self.results["schema_version"], results_mod.RESULTS_SCHEMA_VERSION)
        for key in (
            "experiment",
            "run",
            "started_at",
            "commit",
            "config",
            "environment",
            "k_values",
            "profiles",
            "degradation",
            "summary",
        ):
            self.assertIn(key, self.results)
        self.assertEqual(self.results["k_values"], list(K_VALUES))

    def test_degradation_covers_shared_answerable_questions(self):
        entry = self.results["degradation"]["single-project->multi-project"]
        self.assertEqual(entry["questions_compared"], ["a", "b"])
        # single recall@7 = 7/20; multi returns nothing (scale 0).
        self.assertAlmostEqual(entry["recall@7"], -0.35)
        self.assertAlmostEqual(entry["purity@7"], -0.35)
        self.assertAlmostEqual(entry["p95_ms"], 0.0)

    def test_summary_counts_and_no_hit_rate(self):
        single = self.results["summary"]["single-project"]
        self.assertEqual(single["answerable"], 2)
        self.assertEqual(single["unanswerable"], 0)
        self.assertIsNone(single["unanswerable_no_hit_rate"])
        self.assertAlmostEqual(single["mean_recall"]["recall@7"], 0.35)
        self.assertAlmostEqual(single["mean_purity@7"], 0.35)

        multi = self.results["summary"]["multi-project"]
        self.assertEqual(multi["answerable"], 2)
        self.assertEqual(multi["unanswerable"], 1)
        self.assertEqual(multi["unanswerable_no_hit_rate"], 1.0)
        self.assertEqual(multi["mean_recall"]["recall@7"], 0.0)


class SubsetSweepTest(unittest.TestCase):
    """Regression: a `--k 1`-style subset must not KeyError in aggregation."""

    def test_build_results_with_single_k(self):
        results = build((1,), latency_k=1)
        self.assertEqual(results["k_values"], [1])

        single = results["summary"]["single-project"]
        self.assertEqual(list(single["mean_recall"]), ["recall@1"])
        self.assertIn("mean_purity@1", single)
        self.assertIn("mean_mrr@1", single)

        entry = results["degradation"]["single-project->multi-project"]
        self.assertIn("recall@1", entry)
        self.assertIn("purity@1", entry)
        self.assertNotIn("recall@3", entry)

    def test_build_results_with_multi_k_subset(self):
        results = build((1, 5, 20), latency_k=5)
        self.assertEqual(results["k_values"], [1, 5, 20])
        self.assertEqual(
            list(results["summary"]["single-project"]["mean_recall"]),
            ["recall@1", "recall@5", "recall@20"],
        )


class LatencyKTest(unittest.TestCase):
    """Regression: aggregation must use the run's latency_k, not the default 7."""

    def test_summary_reads_labels_at_latency_k(self):
        results = build(K_VALUES, latency_k=10)
        single = results["summary"]["single-project"]
        self.assertIn("mean_purity@10", single)
        self.assertNotIn("mean_purity@7", single)
        # answered_entry(k=10) with scale 1.0 -> 10/20.
        self.assertAlmostEqual(single["mean_purity@10"], 0.5)
        self.assertAlmostEqual(single["mean_mrr@10"], 0.5)

    def test_degradation_uses_latency_k(self):
        entry = build(K_VALUES, latency_k=10)["degradation"]["single-project->multi-project"]
        self.assertIn("purity@10", entry)
        self.assertNotIn("purity@7", entry)


class ValidateMetricKTest(unittest.TestCase):
    def test_normalizes_sorts_dedupes(self):
        self.assertEqual(results_mod.validate_metric_k([5, 1, 3, 1], 1), (1, 3, 5))

    def test_latency_k_must_be_swept(self):
        with self.assertRaises(ValueError):
            results_mod.validate_metric_k((1, 3, 5), 7)

    def test_empty_sweep_rejected(self):
        with self.assertRaises(ValueError):
            results_mod.validate_metric_k((), 7)

    def test_build_results_rejects_latency_outside_sweep(self):
        with self.assertRaises(ValueError):
            build((1, 3, 5), latency_k=7)


class WriteResultsTest(unittest.TestCase):
    def test_writes_pretty_json_and_hits(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "nested" / "run"
            path = results_mod.write_results(run_dir, {"schema_version": 1})
            self.assertTrue(path.is_file())
            self.assertTrue(path.read_text().endswith("\n"))
            self.assertEqual(json.loads(path.read_text())["schema_version"], 1)

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

import copy
import json
import unittest
from pathlib import Path

from eval.report.framework import (
    Baseline,
    ResultsError,
    UnsupportedIngestSchemaVersion,
    UnsupportedSchemaVersion,
    load_ingest,
    load_results,
    render_report,
)

FIXTURES = Path(__file__).parent / "fixtures"


def render_fixture(name):
    return render_report(load_results(FIXTURES / name / "results.json"))


class RenderCurrentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = render_fixture("v2")

    def test_is_a_complete_document(self):
        self.assertTrue(self.html.startswith("<!doctype html>"))
        self.assertIn("</html>", self.html)

    def test_carries_provenance(self):
        self.assertIn("baseline", self.html)
        self.assertIn("f57809d1c2b3a4d5e6f708192a3b4c5d6e7f8091", self.html)
        self.assertIn("origin/main", self.html)

    def test_has_the_expected_sections(self):
        for marker in (
            "Provenance",
            "Summary",
            "Retrieval quality",
            "Timing",
            "Cross-corpus degradation",
            "Appendix",
            "Methodology",
        ):
            self.assertIn(marker, self.html)
        self.assertGreaterEqual(self.html.count("<svg"), 2)

    def test_shows_question_ids(self):
        self.assertIn("jj-author", self.html)
        self.assertIn("unanswerable-license", self.html)

    def test_renders_precision_at_every_k(self):
        # precision is a contract metric, so it must be inspectable per k
        for k in (3, 7, 20):
            self.assertIn(f"precision@{k}", self.html)

    def test_has_one_per_question_table_per_k(self):
        for k in (3, 7, 20):
            self.assertIn(f"<h4>k={k}</h4>", self.html)

    def test_timing_section_has_three_phase_charts_and_rerank_cost(self):
        timing = self.html.split('id="timing"', 1)[1].split('id="degradation"', 1)[0]
        self.assertEqual(timing.count("<svg"), 3)
        self.assertIn("Rerank cost", timing)
        self.assertIn("failures", timing)

    def test_quality_metrics_are_not_reported_at_a_single_latency_k(self):
        self.assertNotIn("Latency measured at", self.html)
        self.assertNotIn("latency k", self.html.lower())

    def test_subsection_headings_do_not_skip_a_level(self):
        # each section is an <h2>, so its subsections must be <h3>, not <h4>
        self.assertIn("<h3>Environment</h3>", self.html)
        self.assertNotIn("<h4>Environment</h4>", self.html)
        self.assertNotIn("<h4>single-project</h4>", self.html)
        self.assertNotIn("<h4>multi-project</h4>", self.html)

    def test_is_self_contained(self):
        lowered = self.html.lower()
        for needle in ("<script", "<link", "@import", 'src="http', 'href="http', "url(http"):
            self.assertNotIn(needle, lowered)

    def test_escapes_untrusted_text(self):
        results = load_results(FIXTURES / "v2" / "results.json")
        results["experiment"] = "<script>alert(1)</script>"
        html = render_report(results)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_title_override_is_escaped(self):
        results = load_results(FIXTURES / "v2" / "results.json")
        html = render_report(results, title="Custom <title>")
        self.assertIn("<title>Custom &lt;title&gt;</title>", html)

    def test_notes_render(self):
        results = load_results(FIXTURES / "v2" / "results.json")
        html = render_report(results, notes=["drop small chunks"])
        self.assertIn("drop small chunks", html)

    def test_unknown_version_is_refused(self):
        results = load_results(FIXTURES / "v2" / "results.json")
        results["schema_version"] = 99
        with self.assertRaises(UnsupportedSchemaVersion):
            render_report(results)

    def test_degradation_deltas_are_formatted(self):
        results = load_results(FIXTURES / "v2" / "results.json")
        entry = results["degradation"]["single-project->multi-project"]
        entry["p95_ms"] = 1.0298999999999996
        entry["recall@7"] = -0.33333333333333337
        html = render_report(results)
        self.assertNotIn("1.0298999999999996", html)
        self.assertNotIn("0.33333333333333337", html)
        self.assertIn("1.03", html)
        self.assertIn("-0.333", html)


class RenderV1RefusalTest(unittest.TestCase):
    def test_v1_results_are_refused(self):
        results = load_results(FIXTURES / "v2" / "results.json")
        results["schema_version"] = 1
        with self.assertRaises(UnsupportedSchemaVersion) as caught:
            render_report(results)
        self.assertIn("not comparable", str(caught.exception))


class RenderIngestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = load_results(FIXTURES / "v2" / "results.json")
        cls.ingest = load_ingest(FIXTURES / "ingest" / "v1" / "ingest-single-project.json")
        cls.reused = load_ingest(FIXTURES / "ingest" / "v1_reused" / "ingest-single-project.json")

    def test_combined_render_has_both_parts(self):
        html = render_report(self.results, ingest=[self.ingest])
        self.assertIn('id="ingestion"', html)
        self.assertIn("Retrieval quality", html)
        self.assertIn("status: created", html)
        self.assertIn("Chunk-token distribution", html)
        self.assertIn("&lt;50", html)  # the bucket label is escaped
        self.assertIn("Min chunk tokens", html)
        self.assertIn("phase timeline", html.lower())

    def test_ingest_only_renders_with_declared_commit(self):
        html = render_report(None, ingest=[self.ingest], commit="deadbeef", experiment="drop-small-chunks")
        self.assertIn("deadbeef", html)
        self.assertIn("drop-small-chunks", html)
        self.assertIn('id="ingestion"', html)
        self.assertNotIn("Retrieval quality", html)

    def test_reused_is_marked_cost_not_measured(self):
        html = render_report(None, ingest=[self.reused], commit="deadbeef")
        self.assertIn("Cost not measured", html)
        # A reused collection records nothing, so the zero cost tables stay out.
        self.assertNotIn("Chunk-token distribution", html)
        self.assertNotIn(">Embeddings<", html)
        self.assertNotIn(">Timing<", html)

    def test_combined_is_self_contained(self):
        html = render_report(self.results, ingest=[self.ingest, self.reused])
        lowered = html.lower()
        for needle in ("<script", "<link", "@import", 'src="http', 'href="http', "url(http"):
            self.assertNotIn(needle, lowered)

    def test_unknown_ingest_version_is_refused(self):
        bad = dict(self.ingest)
        bad["schema_version"] = 99
        with self.assertRaises(UnsupportedIngestSchemaVersion):
            render_report(self.results, ingest=[bad])

    def test_nothing_to_render(self):
        with self.assertRaises(ResultsError):
            render_report(None)


class NumberFormatTest(unittest.TestCase):
    def test_zero_digit_integers_keep_trailing_zeros(self):
        from eval.report.framework.render import _number

        self.assertEqual(_number(550.0, digits=0), "550")
        self.assertEqual(_number(40290, digits=0), "40290")
        self.assertEqual(_number(100.0, digits=0), "100")

    def test_fractional_zeros_are_trimmed(self):
        from eval.report.framework.render import _number

        self.assertEqual(_number(0.3333333, digits=3), "0.333")
        self.assertEqual(_number(100.0), "100")


class RenderModesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = load_results(FIXTURES / "v3" / "results.json")
        cls.html = render_report(cls.results)

    def test_provenance_lists_every_measured_mode(self):
        self.assertIn("hybrid, dense, sparse", self.html)

    def test_has_the_search_modes_section_after_summary(self):
        self.assertIn('id="modes"', self.html)
        self.assertIn("Search modes", self.html)
        self.assertLess(
            self.html.index('id="summary"'), self.html.index('id="modes"')
        )
        self.assertLess(
            self.html.index('id="modes"'), self.html.index('id="quality"')
        )

    def test_summary_is_one_mode_by_metric_table_per_profile(self):
        summary = self.html.split('id="summary"', 1)[1].split('id="modes"', 1)[0]
        self.assertEqual(summary.count("<h3>"), 2)
        self.assertIn("recall@20", summary)
        self.assertIn("purity@20", summary)
        self.assertIn("MRR@20", summary)
        self.assertIn("no-hit", summary)
        self.assertIn(">hybrid<", summary)
        self.assertIn(">dense<", summary)
        self.assertIn(">sparse<", summary)
        # Counts are shown once per profile, not once per mode.
        self.assertEqual(summary.count("<th scope=\"row\">Questions</th>"), 2)

    def test_modes_section_compares_recall_purity_mrr_and_latency(self):
        modes = self.html.split('id="modes"', 1)[1].split('id="quality"', 1)[0]
        self.assertIn("Δ dense", modes)
        self.assertIn("Δ sparse", modes)
        self.assertIn("recall@3", modes)
        self.assertIn("MRR@20", modes)
        self.assertIn("rerank cost RUB", modes)
        self.assertIn("Per question at k=20", modes)
        self.assertEqual(modes.count("<svg"), 2)  # one chart per profile

    def test_quality_tables_are_collapsed_per_profile_and_mode(self):
        quality = self.html.split('id="quality"', 1)[1].split('id="timing"', 1)[0]
        self.assertEqual(quality.count("<details>"), 6)
        self.assertNotIn("<details open", quality)
        for k in (3, 7, 20):
            self.assertIn(f"<h4>k={k}</h4>", quality)

    def test_timing_labels_include_profile_and_mode(self):
        timing = self.html.split('id="timing"', 1)[1].split('id="degradation"', 1)[0]
        self.assertIn("single-project · hybrid", timing)
        self.assertIn("multi-project · sparse", timing)
        self.assertIn(">Mode<", timing)
        self.assertEqual(timing.count("<svg"), 3)

    def test_degradation_is_per_mode(self):
        degradation = self.html.split('id="degradation"', 1)[1]
        for mode in ("hybrid", "dense", "sparse"):
            self.assertIn(f"<td>{mode}</td>", degradation)
        self.assertIn("Δ e2e p95 ms", degradation)

    def test_single_mode_run_omits_the_modes_section(self):
        results = copy.deepcopy(self.results)
        results["modes"] = ["hybrid"]
        for profile in results["profiles"].values():
            profile["modes"] = {"hybrid": profile["modes"]["hybrid"]}
            for question in profile["questions"]:
                question["modes"] = {"hybrid": question["modes"]["hybrid"]}
        for entry in results["summary"].values():
            entry["modes"] = {"hybrid": entry["modes"]["hybrid"]}
        results["degradation"]["single-project->multi-project"]["modes"] = {
            "hybrid": results["degradation"]["single-project->multi-project"]["modes"]["hybrid"]
        }
        html = render_report(results)
        self.assertNotIn('id="modes"', html)
        self.assertNotIn("Search modes", html)


class RenderV2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = render_fixture("v2")

    def test_provenance_says_the_mode_was_not_recorded(self):
        self.assertIn("not recorded (schema v2)", self.html)

    def test_omits_the_modes_section(self):
        self.assertNotIn('id="modes"', self.html)
        self.assertNotIn("Search modes", self.html)

    def test_single_unknown_series_is_rendered(self):
        self.assertIn(">unknown<", self.html)
        self.assertEqual(self.html.count("<details>"), 2)


class RenderComparisonTest(unittest.TestCase):
    def setUp(self):
        self.baseline_results = load_results(FIXTURES / "v2" / "results.json")
        self.baseline_ingest = load_ingest(FIXTURES / "ingest" / "v1" / "ingest-single-project.json")
        # A deep copy the test can perturb without touching the fixture.
        self.candidate_results = json.loads(json.dumps(self.baseline_results))
        self.candidate_ingest = json.loads(json.dumps(self.baseline_ingest))

    @property
    def baseline(self):
        return Baseline(
            label="baseline (2026-01-01T00-00-00Z)",
            results=self.baseline_results,
            ingest=(self.baseline_ingest,),
        )

    def test_shows_cost_and_retrieval_deltas(self):
        self.candidate_ingest["totals"]["embeddings"] = (
            self.baseline_ingest["totals"]["embeddings"] - 50
        )
        html = render_report(
            self.candidate_results,
            ingest=[self.candidate_ingest],
            baseline=self.baseline,
            experiment="candidate",
        )
        self.assertIn('id="comparison"', html)
        self.assertIn("candidate vs baseline", html)
        self.assertIn("stroke-dasharray", html)  # baseline series is dashed
        self.assertIn("-50", html)  # embeddings delta, trailing zero preserved

    def test_reused_baseline_is_flagged(self):
        reused = load_ingest(FIXTURES / "ingest" / "v1_reused" / "ingest-single-project.json")
        baseline = Baseline(label="baseline", results=self.baseline_results, ingest=(reused,))
        html = render_report(self.candidate_results, ingest=[self.candidate_ingest], baseline=baseline)
        self.assertIn("Baseline cost not measured", html)

    def test_without_baseline_there_is_no_comparison(self):
        html = render_report(self.candidate_results, ingest=[self.candidate_ingest])
        self.assertNotIn('id="comparison"', html)

    def test_ingest_only_candidate_uses_the_declared_commit(self):
        html = render_report(
            None,
            ingest=[self.candidate_ingest],
            baseline=self.baseline,
            commit="deadbeef",
            experiment="candidate",
        )
        self.assertIn('id="comparison"', html)
        # Provenance and the comparison's candidate-commit cell must agree.
        self.assertGreaterEqual(html.count("deadbeef"), 2)

    def test_v3_retrieval_is_compared_per_mode(self):
        v3 = load_results(FIXTURES / "v3" / "results.json")
        html = render_report(v3, baseline=Baseline("v3 baseline", v3))
        comparison = html.split('id="comparison"', 1)[1]
        for mode in ("hybrid", "dense", "sparse"):
            self.assertIn(f"<h3>Mode: {mode}</h3>", comparison)
        self.assertIn("<th>Mode</th><th>Profile</th><th>id</th>", comparison)

    def test_v3_candidate_against_a_v2_baseline_warns(self):
        v3 = load_results(FIXTURES / "v3" / "results.json")
        v2 = load_results(FIXTURES / "v2" / "results.json")
        html = render_report(
            v3,
            baseline=Baseline("v2 baseline", v2, ingest=(self.baseline_ingest,)),
        )
        self.assertIn("Baseline records no search mode (schema v2)", html)
        self.assertIn("cannot be paired until it is re-recorded", html)

    def test_unmatched_modes_are_warned_about(self):
        v3 = load_results(FIXTURES / "v3" / "results.json")
        subset = copy.deepcopy(v3)
        subset["modes"] = ["hybrid"]
        for profile in subset["profiles"].values():
            profile["modes"] = {"hybrid": profile["modes"]["hybrid"]}
            for question in profile["questions"]:
                question["modes"] = {"hybrid": question["modes"]["hybrid"]}
        for entry in subset["summary"].values():
            entry["modes"] = {"hybrid": entry["modes"]["hybrid"]}
        subset["degradation"]["single-project->multi-project"]["modes"] = {
            "hybrid": subset["degradation"]["single-project->multi-project"]["modes"]["hybrid"]
        }
        html = render_report(subset, baseline=Baseline("v3 baseline", v3))
        self.assertIn("Baseline-only series (no candidate match)", html)
        self.assertIn("multi-project · sparse", html)

    def test_comparison_is_self_contained(self):
        html = render_report(
            self.candidate_results,
            ingest=[self.candidate_ingest],
            baseline=self.baseline,
        )
        lowered = html.lower()
        for needle in ("<script", "<link", "@import", 'src="http', 'href="http', "url(http"):
            self.assertNotIn(needle, lowered)


if __name__ == "__main__":
    unittest.main()

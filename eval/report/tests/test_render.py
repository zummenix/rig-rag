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
        cls.html = render_fixture("v1")

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
            "Latency",
            "Cross-corpus degradation",
            "Appendix",
            "Methodology",
        ):
            self.assertIn(marker, self.html)
        self.assertGreaterEqual(self.html.count("<svg"), 2)

    def test_shows_question_ids(self):
        self.assertIn("jj-author", self.html)
        self.assertIn("unanswerable-license", self.html)

    def test_renders_precision_at_the_latency_k(self):
        # precision is a contract metric, so it must be inspectable in the table
        self.assertIn("precision@7", self.html)

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
        results = load_results(FIXTURES / "v1" / "results.json")
        results["experiment"] = "<script>alert(1)</script>"
        html = render_report(results)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_title_override_is_escaped(self):
        results = load_results(FIXTURES / "v1" / "results.json")
        html = render_report(results, title="Custom <title>")
        self.assertIn("<title>Custom &lt;title&gt;</title>", html)

    def test_notes_render(self):
        results = load_results(FIXTURES / "v1" / "results.json")
        html = render_report(results, notes=["drop small chunks"])
        self.assertIn("drop small chunks", html)

    def test_unknown_version_is_refused(self):
        results = load_results(FIXTURES / "v1" / "results.json")
        results["schema_version"] = 99
        with self.assertRaises(UnsupportedSchemaVersion):
            render_report(results)

    def test_degradation_deltas_are_formatted(self):
        results = load_results(FIXTURES / "v1" / "results.json")
        entry = results["degradation"]["single-project->multi-project"]
        entry["p95_ms"] = 1.0298999999999996
        entry["recall@7"] = -0.33333333333333337
        html = render_report(results)
        self.assertNotIn("1.0298999999999996", html)
        self.assertNotIn("0.33333333333333337", html)
        self.assertIn("1.03", html)
        self.assertIn("-0.333", html)


class RenderLegacyTest(unittest.TestCase):
    def test_legacy_fixture_renders(self):
        html = render_fixture("v1_legacy")
        self.assertIn("<svg", html)
        # The swept k set is derived from the file; both labels reach the chart.
        self.assertIn(">1<", html)
        self.assertIn(">7<", html)


class RenderIngestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = load_results(FIXTURES / "v1" / "results.json")
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


class RenderComparisonTest(unittest.TestCase):
    def setUp(self):
        self.baseline_results = load_results(FIXTURES / "v1" / "results.json")
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

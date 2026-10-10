import unittest
from pathlib import Path

from eval.report.framework import UnsupportedSchemaVersion, load_results, render_report

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


if __name__ == "__main__":
    unittest.main()

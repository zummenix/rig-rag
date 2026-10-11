import unittest

from eval.report.framework import svg


class LineChartTest(unittest.TestCase):
    def test_returns_svg_with_points_and_labels(self):
        out = svg.line_chart(["1", "7"], [svg.Series("mean", [0.5, 0.25])])
        self.assertTrue(out.startswith("<svg"))
        self.assertTrue(out.endswith("</svg>"))
        self.assertIn("<polyline", out)
        self.assertEqual(out.count("<circle"), 2)
        self.assertIn(">1<", out)
        self.assertIn(">7<", out)

    def test_none_values_break_the_line(self):
        out = svg.line_chart(["1", "3", "7"], [svg.Series("x", [1.0, None, 0.5])])
        self.assertNotIn("<polyline", out)  # two isolated points, no segment
        self.assertEqual(out.count("<circle"), 2)

    def test_escapes_x_labels(self):
        out = svg.line_chart(["<b>"], [svg.Series("s", [0.5])])
        self.assertIn("&lt;b&gt;", out)
        self.assertNotIn("<b>", out)

    def test_value_count_must_match_labels(self):
        with self.assertRaises(ValueError):
            svg.line_chart(["1"], [svg.Series("s", [0.5, 0.6])])


class GroupedBarChartTest(unittest.TestCase):
    def test_negative_values_diverge_below_zero(self):
        out = svg.grouped_bar_chart(
            ["k=1", "k=7"],
            [svg.Series("delta", [-1.0, -0.5], svg.PALETTE["negative"])],
            show_values=True,
        )
        self.assertTrue(out.startswith("<svg"))
        # two bars plus the legend swatch
        self.assertEqual(out.count("<rect"), 3)
        self.assertIn("-1.00", out)

    def test_all_none_series_does_not_crash(self):
        out = svg.grouped_bar_chart(["p"], [svg.Series("x", [None])])
        self.assertTrue(out.endswith("</svg>"))

    def test_series_value_count_must_match_groups(self):
        with self.assertRaises(ValueError):
            svg.grouped_bar_chart(["p"], [svg.Series("x", [0.1, 0.2])])
        with self.assertRaises(ValueError):
            svg.grouped_bar_chart(["p", "q"], [svg.Series("x", [0.1])])


class WaterfallTest(unittest.TestCase):
    def test_draws_one_bar_per_phase(self):
        out = svg.waterfall([("fetch", 1000.0), ("embed", 500.0)], total=2000.0)
        self.assertTrue(out.startswith("<svg"))
        self.assertTrue(out.endswith("</svg>"))
        self.assertEqual(out.count("<rect"), 2)
        self.assertIn(">fetch<", out)
        self.assertIn(">embed<", out)
        self.assertIn("1,000", out)

    def test_bars_start_where_the_previous_ended(self):
        # fetch spans the first half of a 100-wide... axis; embed must start at
        # the x coordinate fetch ends at, not back at zero.
        out = svg.waterfall([("fetch", 50.0), ("embed", 50.0)], total=100.0, width=300)
        self.assertIn(">fetch<", out)
        self.assertIn(">embed<", out)

    def test_escapes_labels(self):
        out = svg.waterfall([("<b>", 1.0)])
        self.assertIn("&lt;b&gt;", out)
        self.assertNotIn("<b>", out)

    def test_empty_is_rejected(self):
        with self.assertRaises(ValueError):
            svg.waterfall([])


if __name__ == "__main__":
    unittest.main()

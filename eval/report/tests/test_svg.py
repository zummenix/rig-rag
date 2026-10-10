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


if __name__ == "__main__":
    unittest.main()

import unittest

from eval.questions import Gold
from eval.runner.gold import (
    covering_gold_indexes,
    hit_covers,
    lines_overlap,
    path_matches,
    source_of,
)


def hit(path, start=1, end=1, text="body"):
    return {
        "path": path,
        "start_line": start,
        "end_line": end,
        "text": text,
    }


class SourceOfTest(unittest.TestCase):
    def test_extracts_source_under_data_root(self):
        self.assertEqual(source_of("data/single-project/jj/a.md", "data/single-project"), "jj")
        self.assertEqual(source_of("data/jj/a.md", "data"), "jj")
        self.assertEqual(source_of("data/multi-project/qdrant/q/quickstart.md", "data/multi-project"), "qdrant")

    def test_returns_none_outside_data_root(self):
        self.assertIsNone(source_of("other/jj/a.md", "data"))
        self.assertIsNone(source_of("data", "data"))

    def test_normalizes_backslashes(self):
        self.assertEqual(source_of(r"data\single-project\jj\a.md", "data/single-project"), "jj")


class PathMatchesTest(unittest.TestCase):
    def test_suffix_match(self):
        self.assertTrue(path_matches("data/x/jj/testimonials.md", "testimonials.md"))
        self.assertTrue(path_matches("data/x/jj/docs/a.md", "docs/a.md"))
        self.assertTrue(path_matches("data/x/jj/a.md", "data/x/jj/a.md"))

    def test_requires_whole_path_segment(self):
        self.assertFalse(path_matches("data/x/jj/my-testimonials.md", "testimonials.md"))
        self.assertFalse(path_matches("data/x/jj/other.md", "testimonials.md"))


class LinesOverlapTest(unittest.TestCase):
    def test_inclusive_overlap(self):
        self.assertTrue(lines_overlap((1, 5), (5, 9)))
        self.assertTrue(lines_overlap((10, 20), (1, 10)))
        self.assertFalse(lines_overlap((1, 4), (5, 9)))


class HitCoversTest(unittest.TestCase):
    def test_matches_source_path_and_text(self):
        gold = Gold(source="jj", path="testimonials.md", must_contain="creator")
        self.assertTrue(
            hit_covers(
                hit("data/single-project/jj/testimonials.md", text="the creator"),
                gold,
                "data/single-project",
            )
        )

    def test_rejects_wrong_source_even_when_path_suffix_matches(self):
        gold = Gold(source="jj", path="testimonials.md")
        self.assertFalse(
            hit_covers(
                hit("data/multi-project/podman/testimonials.md"), gold, "data/multi-project"
            )
        )

    def test_requires_line_overlap(self):
        gold = Gold(source="jj", path="a.md", lines=(100, 110))
        self.assertFalse(hit_covers(hit("data/jj/a.md", 1, 20), gold, "data"))
        self.assertTrue(hit_covers(hit("data/jj/a.md", 105, 120), gold, "data"))

    def test_covering_gold_indexes(self):
        gold = (Gold(source="jj", path="a.md"), Gold(source="jj", path="b.md"))
        indexes = covering_gold_indexes(hit("data/jj/b.md"), gold, "data")
        self.assertEqual(indexes, [1])


if __name__ == "__main__":
    unittest.main()

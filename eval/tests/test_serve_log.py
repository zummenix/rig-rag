import tempfile
import unittest
import warnings
from pathlib import Path

from eval.runner import serve_log


def write_log(text: str) -> Path:
    directory = Path(tempfile.mkdtemp())
    path = directory / "serve.log"
    path.write_text(text, encoding="utf-8")
    return path


class QuestionHashTest(unittest.TestCase):
    def test_matches_the_rust_contract(self):
        # Same literal question the Rust unit test pins.
        self.assertEqual(
            serve_log.question_hash("Who is the author of jj?"), "3c8d50cdcb9b43f7"
        )


class ParseLogTest(unittest.TestCase):
    def test_joins_search_and_rerank_by_hash_and_mode(self):
        q = serve_log.question_hash("Who wrote jj?")
        records = serve_log.parse_log(
            write_log(
                "rig-rag serving on http://127.0.0.1:1\n"
                f"search q={q} k=20 mode=hybrid candidates=30 duration_ms=412\n"
                f"rerank q={q} mode=hybrid model=rerank-3 docs=30 results=30 "
                "duration_ms=1188 status=success tokens=39 cost=0.000351 cost_rub=0.03\n"
            )
        )

        record = records[q]["hybrid"]
        self.assertEqual(record["search"]["k"], 20)
        self.assertEqual(record["search"]["mode"], "hybrid")
        self.assertEqual(record["search"]["candidates"], 30)
        self.assertEqual(record["search"]["duration_ms"], 412)
        rerank = record["rerank"]
        self.assertEqual(rerank["status"], "success")
        self.assertEqual(rerank["tokens"], 39)
        self.assertAlmostEqual(rerank["cost"], 0.000351)
        self.assertEqual(rerank["duration_ms"], 1188)

    def test_keeps_one_record_per_mode(self):
        q = serve_log.question_hash("Who wrote jj?")
        records = serve_log.parse_log(
            write_log(
                f"search q={q} k=20 mode=hybrid candidates=30 duration_ms=100\n"
                f"search q={q} k=20 mode=dense candidates=30 duration_ms=300\n"
                f"search q={q} k=20 mode=sparse candidates=30 duration_ms=200\n"
            )
        )

        self.assertEqual(set(records[q]), {"hybrid", "dense", "sparse"})
        self.assertEqual(records[q]["hybrid"]["search"]["duration_ms"], 100)
        self.assertEqual(records[q]["dense"]["search"]["duration_ms"], 300)
        self.assertEqual(records[q]["sparse"]["search"]["duration_ms"], 200)
        self.assertEqual(serve_log.modes_in(records), ("hybrid", "dense", "sparse"))

    def test_identity_fields_stay_strings(self):
        # A 16-char hash can look numeric ("7812897326268e41") or be all digits;
        # coercing either would silently drop the question from the metrics.
        path = write_log(
            "search q=7812897326268e41 k=20 mode=hybrid candidates=30 duration_ms=412\n"
            "rerank q=7812897326268e41 mode=hybrid model=rerank-3 docs=30 results=30 "
            "duration_ms=1188 status=success tokens=39 cost=0.000351 cost_rub=0.03\n"
            "search q=1234567890123456 k=20 mode=dense candidates=30 duration_ms=200\n"
        )
        records = serve_log.parse_log(path)

        self.assertEqual(set(records), {"7812897326268e41", "1234567890123456"})
        self.assertIsInstance(next(iter(records)), str)
        search = records["7812897326268e41"]["hybrid"]["search"]
        self.assertEqual(search["q"], "7812897326268e41")
        self.assertIsInstance(search["q"], str)
        self.assertEqual(search["mode"], "hybrid")
        self.assertEqual(records["7812897326268e41"]["hybrid"]["rerank"]["model"], "rerank-3")
        self.assertEqual(
            records["1234567890123456"]["dense"]["search"]["q"], "1234567890123456"
        )

    def test_error_tail_keeps_spaces(self):
        q = serve_log.question_hash("q")
        records = serve_log.parse_log(
            write_log(
                f"rerank q={q} mode=hybrid model=rerank-3 docs=30 results=0 "
                "duration_ms=2001 status=failed error=rerank endpoint returned 500: oh no\n"
            )
        )
        self.assertEqual(
            records[q]["hybrid"]["rerank"]["error"],
            "rerank endpoint returned 500: oh no",
        )

    def test_duplicate_phase_in_one_mode_warns_and_keeps_the_last(self):
        q = serve_log.question_hash("q")
        path = write_log(
            f"search q={q} k=20 mode=hybrid candidates=30 duration_ms=100\n"
            f"search q={q} k=20 mode=hybrid candidates=30 duration_ms=200\n"
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            records = serve_log.parse_log(path)
        self.assertEqual(records[q]["hybrid"]["search"]["duration_ms"], 200)
        self.assertTrue(any("duplicate search" in str(item.message) for item in caught))
        self.assertTrue(any("mode hybrid" in str(item.message) for item in caught))

    def test_same_phase_in_different_modes_does_not_warn(self):
        q = serve_log.question_hash("q")
        path = write_log(
            f"search q={q} k=20 mode=hybrid candidates=30 duration_ms=100\n"
            f"search q={q} k=20 mode=dense candidates=30 duration_ms=200\n"
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            serve_log.parse_log(path)
        self.assertFalse(any("duplicate" in str(item.message) for item in caught))

    def test_lines_without_mode_are_filed_under_unknown(self):
        q = serve_log.question_hash("q")
        records = serve_log.parse_log(
            write_log(f"search q={q} k=20 candidates=30 duration_ms=100\n")
        )
        self.assertIn("unknown", records[q])


class RerankStatsTest(unittest.TestCase):
    def test_sums_one_mode_only(self):
        first = serve_log.question_hash("one")
        second = serve_log.question_hash("two")
        records = serve_log.parse_log(
            write_log(
                f"rerank q={first} mode=hybrid model=rerank-3 docs=30 results=30 "
                "duration_ms=2000 status=success tokens=39 cost=0.000351 cost_rub=0.03\n"
                f"rerank q={second} mode=hybrid model=rerank-3 docs=30 results=30 "
                "duration_ms=2400 status=success tokens=41 cost=0.0004 cost_rub=0.04\n"
                f"rerank q={first} mode=dense model=rerank-3 docs=30 results=30 "
                "duration_ms=900 status=success tokens=5 cost=0.00005 cost_rub=0.01\n"
            )
        )

        stats = serve_log.rerank_stats(records, "hybrid")
        self.assertEqual(stats["calls"], 2)
        self.assertEqual(stats["documents"], 60)
        self.assertEqual(stats["tokens"], 80)
        self.assertAlmostEqual(stats["cost"], 0.000751)
        self.assertAlmostEqual(stats["cost_rub"], 0.07)
        self.assertEqual(stats["p50_ms"], 2200.0)
        self.assertEqual(stats["p95_ms"], 2380.0)
        self.assertEqual(stats["failures"], 0)

        dense = serve_log.rerank_stats(records, "dense")
        self.assertEqual(dense["calls"], 1)
        self.assertEqual(dense["tokens"], 5)

    def test_failed_calls_count_as_calls_and_contribute_docs(self):
        q = serve_log.question_hash("q")
        records = serve_log.parse_log(
            write_log(
                f"rerank q={q} mode=sparse model=m docs=5 results=0 duration_ms=10 "
                "status=failed error=boom\n"
            )
        )
        stats = serve_log.rerank_stats(records, "sparse")

        self.assertEqual(stats["calls"], 1)
        self.assertEqual(stats["failures"], 1)
        self.assertEqual(stats["documents"], 5)
        self.assertIsNone(stats["tokens"])
        self.assertIsNone(stats["cost"])

    def test_missing_usage_leaves_optional_totals_none(self):
        q = serve_log.question_hash("q")
        records = serve_log.parse_log(
            write_log(
                f"rerank q={q} mode=hybrid model=m docs=5 results=5 "
                "duration_ms=10 status=success\n"
            )
        )
        stats = serve_log.rerank_stats(records, "hybrid")

        self.assertEqual(stats["calls"], 1)
        self.assertIsNone(stats["tokens"])
        self.assertIsNone(stats["cost"])

    def test_unseen_mode_is_zero_filled(self):
        q = serve_log.question_hash("q")
        records = serve_log.parse_log(
            write_log(f"search q={q} k=20 mode=hybrid candidates=30 duration_ms=10\n")
        )
        stats = serve_log.rerank_stats(records, "sparse")

        self.assertEqual(stats["calls"], 0)
        self.assertEqual(stats["documents"], 0)
        self.assertEqual(stats["failures"], 0)
        self.assertIsNone(stats["p50_ms"])


class SearchStatsTest(unittest.TestCase):
    def test_sums_only_the_requested_modes_search_lines(self):
        first = serve_log.question_hash("one")
        second = serve_log.question_hash("two")
        records = serve_log.parse_log(
            write_log(
                f"search q={first} k=20 mode=hybrid candidates=30 duration_ms=100\n"
                f"search q={second} k=20 mode=dense candidates=30 duration_ms=300\n"
                f"search q={second} k=20 mode=hybrid candidates=30 duration_ms=400\n"
                f"rerank q={second} mode=hybrid model=m docs=30 results=30 "
                "duration_ms=900 status=success tokens=1\n"
            )
        )

        stats = serve_log.search_stats(records, "hybrid")
        self.assertEqual(stats["calls"], 2)
        self.assertEqual(stats["duration_ms"], [100.0, 400.0])
        self.assertEqual(stats["p50_ms"], 250.0)
        self.assertEqual(stats["p95_ms"], 385.0)

        dense = serve_log.search_stats(records, "dense")
        self.assertEqual(dense["duration_ms"], [300.0])


if __name__ == "__main__":
    unittest.main()

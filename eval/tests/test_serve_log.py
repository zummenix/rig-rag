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
    def test_joins_search_and_rerank_by_hash(self):
        q = serve_log.question_hash("Who wrote jj?")
        records = serve_log.parse_log(
            write_log(
                "rig-rag serving on http://127.0.0.1:1\n"
                f"search q={q} k=20 candidates=30 duration_ms=412\n"
                f"rerank q={q} model=rerank-3 docs=30 results=30 duration_ms=1188 "
                "status=success tokens=39 cost=0.000351 cost_rub=0.03\n"
            )
        )

        self.assertEqual(records[q]["search"]["k"], 20)
        self.assertEqual(records[q]["search"]["candidates"], 30)
        self.assertEqual(records[q]["search"]["duration_ms"], 412)
        rerank = records[q]["rerank"]
        self.assertEqual(rerank["status"], "success")
        self.assertEqual(rerank["tokens"], 39)
        self.assertAlmostEqual(rerank["cost"], 0.000351)
        self.assertEqual(rerank["duration_ms"], 1188)

    def test_error_tail_keeps_spaces(self):
        q = serve_log.question_hash("q")
        records = serve_log.parse_log(
            write_log(
                f"rerank q={q} model=rerank-3 docs=30 results=0 duration_ms=2001 "
                "status=failed error=rerank endpoint returned 500: oh no\n"
            )
        )
        self.assertEqual(
            records[q]["rerank"]["error"], "rerank endpoint returned 500: oh no"
        )

    def test_duplicate_phase_warns_and_keeps_the_last(self):
        q = serve_log.question_hash("q")
        path = write_log(
            f"search q={q} k=20 candidates=30 duration_ms=100\n"
            f"search q={q} k=20 candidates=30 duration_ms=200\n"
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            records = serve_log.parse_log(path)
        self.assertEqual(records[q]["search"]["duration_ms"], 200)
        self.assertTrue(any("duplicate search" in str(item.message) for item in caught))

    def test_per_profile_logs_are_keyed_by_hash(self):
        q = serve_log.question_hash("q")
        path = write_log(f"search q={q} k=20 candidates=30 duration_ms=100\n")
        self.assertEqual(serve_log.parse_log(path).keys(), {q})


class RerankStatsTest(unittest.TestCase):
    def test_sums_calls_tokens_costs_and_durations(self):
        first = serve_log.question_hash("one")
        second = serve_log.question_hash("two")
        stats = serve_log.rerank_stats(
            write_log(
                f"rerank q={first} model=rerank-3 docs=30 results=30 duration_ms=2000 "
                "status=success tokens=39 cost=0.000351 cost_rub=0.03\n"
                f"rerank q={second} model=rerank-3 docs=30 results=30 duration_ms=2400 "
                "status=success tokens=41 cost=0.0004 cost_rub=0.04\n"
            )
        )

        self.assertEqual(stats["calls"], 2)
        self.assertEqual(stats["documents"], 60)
        self.assertEqual(stats["tokens"], 80)
        self.assertAlmostEqual(stats["cost"], 0.000751)
        self.assertAlmostEqual(stats["cost_rub"], 0.07)
        self.assertEqual(stats["duration_ms"], [2000.0, 2400.0])
        self.assertEqual(stats["p50_ms"], 2200.0)
        self.assertEqual(stats["failures"], 0)

    def test_failed_calls_count_as_calls_and_contribute_docs(self):
        q = serve_log.question_hash("q")
        stats = serve_log.rerank_stats(
            write_log(
                f"rerank q={q} model=m docs=5 results=0 duration_ms=10 "
                "status=failed error=boom\n"
            )
        )

        self.assertEqual(stats["calls"], 1)
        self.assertEqual(stats["failures"], 1)
        self.assertEqual(stats["documents"], 5)
        self.assertIsNone(stats["tokens"])
        self.assertIsNone(stats["cost"])
        self.assertEqual(stats["duration_ms"], [10.0])

    def test_missing_usage_leaves_optional_totals_none(self):
        q = serve_log.question_hash("q")
        stats = serve_log.rerank_stats(
            write_log(f"rerank q={q} model=m docs=5 results=5 duration_ms=10 status=success\n")
        )

        self.assertEqual(stats["calls"], 1)
        self.assertIsNone(stats["tokens"])
        self.assertIsNone(stats["cost"])
        self.assertEqual(stats["duration_ms"], [10.0])


class SearchStatsTest(unittest.TestCase):
    def test_sums_only_search_lines(self):
        first = serve_log.question_hash("one")
        second = serve_log.question_hash("two")
        stats = serve_log.search_stats(
            write_log(
                f"search q={first} k=20 candidates=30 duration_ms=100\n"
                f"search q={second} k=20 candidates=30 duration_ms=300\n"
                f"rerank q={second} model=m docs=30 results=30 duration_ms=900 "
                "status=success tokens=1\n"
            )
        )

        self.assertEqual(stats["calls"], 2)
        self.assertEqual(stats["duration_ms"], [100.0, 300.0])
        self.assertEqual(stats["p50_ms"], 200.0)
        self.assertEqual(stats["p95_ms"], 290.0)


if __name__ == "__main__":
    unittest.main()

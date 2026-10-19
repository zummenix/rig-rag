import tempfile
import unittest
from pathlib import Path

from eval.runner import serve_log


class RerankStatsTest(unittest.TestCase):
    def _stats(self, text: str) -> dict:
        directory = tempfile.mkdtemp()
        path = Path(directory) / "serve.log"
        path.write_text(text, encoding="utf-8")
        return serve_log.rerank_stats(path)

    def test_sums_calls_tokens_costs_and_durations(self):
        stats = self._stats(
            "rig-rag serving on http://127.0.0.1:1\n"
            "rerank model=rerank-3 docs=30 results=30 duration_ms=2000 tokens=39 cost=0.000351 cost_rub=0.03\n"
            "rerank model=rerank-3 docs=30 results=30 duration_ms=2400 tokens=41 cost=0.0004 cost_rub=0.04\n"
        )

        self.assertEqual(stats["calls"], 2)
        self.assertEqual(stats["documents"], 60)
        self.assertEqual(stats["tokens"], 80)
        self.assertAlmostEqual(stats["cost"], 0.000751)
        self.assertAlmostEqual(stats["cost_rub"], 0.07)
        self.assertEqual(stats["duration_ms"], [2000.0, 2400.0])
        self.assertEqual(stats["p50_ms"], 2200.0)
        self.assertEqual(stats["failures"], 0)

    def test_counts_fallbacks_and_ignores_other_lines(self):
        stats = self._stats(
            "serve: rerank failed (falling back to fusion): boom\n"
            "serve: request failed: nope\n"
        )

        self.assertEqual(stats["calls"], 0)
        self.assertEqual(stats["failures"], 1)
        self.assertIsNone(stats["tokens"])
        self.assertIsNone(stats["cost"])
        self.assertIsNone(stats["p50_ms"])

    def test_missing_usage_leaves_optional_totals_none(self):
        stats = self._stats(
            "rerank model=m docs=5 results=5 duration_ms=10\n"
        )

        self.assertEqual(stats["calls"], 1)
        self.assertIsNone(stats["tokens"])
        self.assertIsNone(stats["cost"])
        self.assertEqual(stats["duration_ms"], [10.0])


if __name__ == "__main__":
    unittest.main()

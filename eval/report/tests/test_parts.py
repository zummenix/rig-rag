import tempfile
import unittest
from pathlib import Path

from eval.report.framework import ResultsError, load_run, resolve_run
from eval.report.framework.parts import discover_run_parts, latest_run

FIXTURES = Path(__file__).parent / "fixtures"
CREATED = FIXTURES / "ingest" / "v1" / "ingest-single-project.json"


def plant(dest: Path, source: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


class DiscoverRunPartsTest(unittest.TestCase):
    def test_finds_both_parts(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            plant(run / "results.json", FIXTURES / "v1" / "results.json")
            plant(run / "ingest-single-project.json", CREATED)
            parts = discover_run_parts(run)
            self.assertTrue(parts.has_retrieval)
            self.assertTrue(parts.has_ingestion)
            self.assertEqual(len(parts.ingest), 1)
            self.assertFalse(parts.is_empty)

    def test_ingest_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            plant(run / "ingest-single-project.json", CREATED)
            parts = discover_run_parts(run)
            self.assertFalse(parts.has_retrieval)
            self.assertTrue(parts.has_ingestion)

    def test_empty_run_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            parts = discover_run_parts(Path(tmp) / "missing")
            self.assertTrue(parts.is_empty)

    def test_ingest_parts_are_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            plant(run / "ingest-multi-project.json", CREATED)
            plant(run / "ingest-single-project.json", CREATED)
            self.assertEqual(
                [path.name for path in discover_run_parts(run).ingest],
                ["ingest-multi-project.json", "ingest-single-project.json"],
            )


class LatestRunTest(unittest.TestCase):
    def test_picks_the_newest_non_empty_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plant(root / "2026-01-01T00-00-00Z" / "results.json", FIXTURES / "v1" / "results.json")
            plant(root / "2026-02-01T00-00-00Z" / "ingest-single-project.json", CREATED)
            # an even newer, empty run must be skipped
            (root / "2026-03-01T00-00-00Z").mkdir()
            parts = latest_run(root)
            self.assertEqual(parts.run_dir.name, "2026-02-01T00-00-00Z")
            self.assertTrue(parts.has_ingestion)

    def test_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ResultsError):
                latest_run(Path(tmp))


class LoadRunTest(unittest.TestCase):
    def test_loads_results_and_ingest_keyed_by_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            plant(run / "results.json", FIXTURES / "v1" / "results.json")
            plant(run / "ingest-single-project.json", CREATED)
            loaded = load_run(run)
            self.assertIsNotNone(loaded.results)
            self.assertIn("baseline", loaded.label)
            self.assertEqual(loaded.ingest_by_profile()["single-project"]["status"], "created")

    def test_empty_run_directory_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ResultsError):
                load_run(Path(tmp) / "missing")

    def test_resolve_by_experiment_id_picks_newest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for run in ("2026-01-01T00-00-00Z", "2026-02-01T00-00-00Z"):
                plant(
                    root / "eval" / "results" / "baseline" / run / "results.json",
                    FIXTURES / "v1" / "results.json",
                )
            loaded = resolve_run("baseline", root=root)
            self.assertIsNotNone(loaded.results)

    def test_resolve_by_run_directory_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            plant(run / "results.json", FIXTURES / "v1" / "results.json")
            self.assertIsNotNone(resolve_run(run, root=tmp).results)

    def test_resolve_by_results_file_uses_its_directory_for_ingest(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            plant(run / "results.json", FIXTURES / "v1" / "results.json")
            plant(run / "ingest-single-project.json", CREATED)
            loaded = resolve_run(run / "results.json", root=tmp)
            self.assertTrue(loaded.ingest_by_profile())


if __name__ == "__main__":
    unittest.main()

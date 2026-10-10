import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path

from eval.report.framework import ResultsError
from eval.report.framework.cli import report_main

FIXTURES = Path(__file__).parent / "fixtures"


@contextlib.contextmanager
def quiet_stdout():
    """`report_main` prints a summary line; keep the test output clean."""

    with contextlib.redirect_stdout(io.StringIO()):
        yield


def make_repo(root: Path, runs, source: str = "v1", experiment: str = "baseline") -> None:
    """Plants `<root>/eval/results/<experiment>/<run>/results.json` for each run."""

    for run in runs:
        run_dir = root / "eval" / "results" / experiment / run
        run_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES / source / "results.json", run_dir / "results.json")


class ReportMainTest(unittest.TestCase):
    def test_writes_default_report_from_the_newest_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_repo(root, ["2026-01-01T00-00-00Z", "2026-02-01T00-00-00Z"])
            with quiet_stdout():
                code = report_main(experiment="baseline", repo_root=root, argv=[])
            self.assertEqual(code, 0)

            out = root / "eval" / "reports" / "baseline.html"
            self.assertTrue(out.is_file())
            html = out.read_text(encoding="utf-8")
            self.assertIn("<!doctype html>", html)
            # the newest run is the render input, recorded repo-relative
            self.assertIn(
                "eval/results/baseline/2026-02-01T00-00-00Z/results.json", html
            )

    def test_results_and_out_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_repo(root, ["2026-01-01T00-00-00Z"])
            other = root / "eval" / "results" / "baseline" / "2025-01-01T00-00-00Z"
            other.mkdir(parents=True)
            shutil.copyfile(
                FIXTURES / "v1_legacy" / "results.json", other / "results.json"
            )
            out = root / "custom" / "report.html"
            with quiet_stdout():
                code = report_main(
                    experiment="baseline",
                    repo_root=root,
                    argv=["--results", str(other / "results.json"), "--out", str(out)],
                )
            self.assertEqual(code, 0)
            self.assertTrue(out.is_file())
            # the default output was not also written
            self.assertFalse((root / "eval" / "reports" / "baseline.html").exists())

    def test_out_parent_directories_are_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_repo(root, ["2026-01-01T00-00-00Z"])
            out = root / "deep" / "nested" / "report.html"
            with quiet_stdout():
                report_main(experiment="baseline", repo_root=root, argv=["--out", str(out)])
            self.assertTrue(out.is_file())

    def test_notes_are_rendered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_repo(root, ["2026-01-01T00-00-00Z"])
            with quiet_stdout():
                report_main(
                    experiment="baseline",
                    repo_root=root,
                    notes=("a narrative note",),
                    argv=[],
                )
            html = (root / "eval" / "reports" / "baseline.html").read_text(encoding="utf-8")
            self.assertIn("a narrative note", html)

    def test_missing_results_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with quiet_stdout(), self.assertRaises(ResultsError):
                report_main(experiment="baseline", repo_root=Path(tmp), argv=[])

    def test_discovers_ingest_in_the_run_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_repo(root, ["2026-01-01T00-00-00Z"])
            run_dir = root / "eval" / "results" / "baseline" / "2026-01-01T00-00-00Z"
            shutil.copyfile(
                FIXTURES / "ingest" / "v1" / "ingest-single-project.json",
                run_dir / "ingest-single-project.json",
            )
            with quiet_stdout():
                report_main(experiment="baseline", repo_root=root, argv=[])
            html = (root / "eval" / "reports" / "baseline.html").read_text(encoding="utf-8")
            self.assertIn('id="ingestion"', html)
            self.assertIn("ingest-single-project.json", html)

    def test_no_ingest_drops_the_ingestion_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_repo(root, ["2026-01-01T00-00-00Z"])
            run_dir = root / "eval" / "results" / "baseline" / "2026-01-01T00-00-00Z"
            shutil.copyfile(
                FIXTURES / "ingest" / "v1" / "ingest-single-project.json",
                run_dir / "ingest-single-project.json",
            )
            with quiet_stdout():
                report_main(experiment="baseline", repo_root=root, argv=["--no-ingest"])
            html = (root / "eval" / "reports" / "baseline.html").read_text(encoding="utf-8")
            self.assertNotIn('id="ingestion"', html)
            self.assertIn("Retrieval quality", html)

    def test_no_retrieval_renders_ingest_only_with_declared_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_repo(root, ["2026-01-01T00-00-00Z"])
            run_dir = root / "eval" / "results" / "baseline" / "2026-01-01T00-00-00Z"
            shutil.copyfile(
                FIXTURES / "ingest" / "v1" / "ingest-single-project.json",
                run_dir / "ingest-single-project.json",
            )
            with quiet_stdout():
                report_main(
                    experiment="baseline",
                    repo_root=root,
                    argv=["--no-retrieval", "--commit", "deadbeef"],
                )
            html = (root / "eval" / "reports" / "baseline.html").read_text(encoding="utf-8")
            self.assertIn("deadbeef", html)
            self.assertNotIn("Retrieval quality", html)
            self.assertIn('id="ingestion"', html)

    def test_both_parts_disabled_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_repo(root, ["2026-01-01T00-00-00Z"])
            with quiet_stdout(), self.assertRaises(ResultsError):
                report_main(
                    experiment="baseline",
                    repo_root=root,
                    argv=["--no-retrieval", "--no-ingest"],
                )

    def test_declared_baseline_renders_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_repo(root, ["2026-01-01T00-00-00Z"], experiment="baseline")
            make_repo(root, ["2026-02-01T00-00-00Z"], experiment="candidate")
            with quiet_stdout():
                report_main(experiment="candidate", baseline="baseline", repo_root=root, argv=[])
            html = (root / "eval" / "reports" / "candidate.html").read_text(encoding="utf-8")
            self.assertIn('id="comparison"', html)

    def test_no_baseline_flag_suppresses_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_repo(root, ["2026-01-01T00-00-00Z"], experiment="baseline")
            make_repo(root, ["2026-02-01T00-00-00Z"], experiment="candidate")
            with quiet_stdout():
                report_main(
                    experiment="candidate",
                    baseline="baseline",
                    repo_root=root,
                    argv=["--no-baseline"],
                )
            html = (root / "eval" / "reports" / "candidate.html").read_text(encoding="utf-8")
            self.assertNotIn('id="comparison"', html)

    def test_baseline_override_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_repo(root, ["2026-01-01T00-00-00Z"], experiment="baseline")
            make_repo(root, ["2026-02-01T00-00-00Z"], experiment="candidate")
            with quiet_stdout():
                report_main(experiment="candidate", repo_root=root, argv=["--baseline", "baseline"])
            html = (root / "eval" / "reports" / "candidate.html").read_text(encoding="utf-8")
            self.assertIn('id="comparison"', html)

    def test_missing_baseline_experiment_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_repo(root, ["2026-02-01T00-00-00Z"], experiment="candidate")
            with quiet_stdout(), self.assertRaises(ResultsError):
                report_main(experiment="candidate", baseline="nope", repo_root=root, argv=[])


if __name__ == "__main__":
    unittest.main()

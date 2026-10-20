import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from eval.questions import Question
from eval.runner import serve_log
from eval.runner.profiles import Profile
from eval.runner.run import (
    RunOptions,
    _environment,
    _sweep,
    load_run_inputs,
    measured_revision,
    require_applicable_questions,
    uncommitted_tracked_changes,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def profile(name: str) -> Profile:
    return Profile(
        name=name,
        config_path=Path("cfg.toml"),
        sources_path=Path("sources.json"),
        config={},
        sources=[],
    )


class LoadRunInputsTest(unittest.TestCase):
    def test_selects_only_requested_profiles(self):
        profiles, _ = load_run_inputs(REPO_ROOT, ["single-project"])
        self.assertEqual([profile.name for profile in profiles], ["single-project"])

    def test_validates_against_all_profiles_when_a_subset_is_selected(self):
        # Regression: a single-profile run must not fail because other
        # questions reference profiles that were not selected.
        profiles, questions = load_run_inputs(REPO_ROOT, ["single-project"])
        self.assertTrue(profiles)
        self.assertTrue(any("multi-project" in q.applies_to for q in questions))

    def test_all_profiles_by_default(self):
        profiles, _ = load_run_inputs(REPO_ROOT, None)
        self.assertEqual(
            [profile.name for profile in profiles], ["multi-project", "single-project"]
        )


class RequireApplicableQuestionsTest(unittest.TestCase):
    def test_rejects_a_profile_with_no_applicable_questions(self):
        only_multi = Question(
            id="q", question="?", applies_to=("multi-project",), gold=()
        )
        with self.assertRaisesRegex(ValueError, "no applicable questions"):
            require_applicable_questions([profile("single-project")], [only_multi])

    def test_accepts_a_profile_with_an_applicable_question(self):
        question = Question(
            id="q", question="?", applies_to=("multi-project",), gold=()
        )
        require_applicable_questions([profile("multi-project")], [question])


class EnvironmentTest(unittest.TestCase):
    def test_binary_is_repo_relative_and_hashed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / "target" / "release" / "rig-rag"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"bin")
            env = _environment(binary, "http://qdrant", root)
            self.assertEqual(env["binary"], "target/release/rig-rag")
            self.assertEqual(
                env["binary_sha256"], hashlib.sha256(b"bin").hexdigest()
            )
            self.assertNotIn(str(root), env["binary"])

    def test_external_binary_path_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            external = Path(tmp) / "rig-rag"
            external.write_bytes(b"bin")
            env = _environment(external, "http://qdrant", root)
            self.assertTrue(env["binary"].endswith("rig-rag"))


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _init_repo(root: Path) -> str:
    """A throwaway repo with one committed file; returns its commit SHA."""

    _git(root, "init", "-q")
    _git(root, "config", "user.email", "eval@example.test")
    _git(root, "config", "user.name", "eval test")
    _git(root, "config", "commit.gpgsign", "false")
    (root / "aaa.txt").write_text("one\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "init")
    return _git(root, "rev-parse", "HEAD")


class MeasuredRevisionTest(unittest.TestCase):
    def test_non_repository_is_best_effort_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(measured_revision(root), ("", ""))
            self.assertEqual(uncommitted_tracked_changes(root), [])

    def test_clean_repository_reads_head_and_reports_no_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            commit = _init_repo(root)
            # No remote refs contain the commit, so `commit_ref` is empty — the
            # runner no longer requires a pushed revision.
            self.assertEqual(measured_revision(root), (commit, ""))
            self.assertEqual(uncommitted_tracked_changes(root), [])

    def test_first_path_is_not_corrupted_by_leading_whitespace(self):
        # Regression: `git status --porcelain`'s first line begins with a space
        # for a worktree-only change; a stripped command output would drop it
        # and mis-slice the path (dropping the leading "e"). `git diff
        # --name-only` avoids that.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            (root / "aaa.txt").write_text("two\n", encoding="utf-8")
            self.assertEqual(uncommitted_tracked_changes(root), ["aaa.txt"])

    def test_untracked_files_are_not_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            (root / "new.txt").write_text("untracked\n", encoding="utf-8")
            self.assertEqual(uncommitted_tracked_changes(root), [])


def hit(index: int, path: str = "data/single-project/jj/a.md") -> dict:
    return {
        "chunk_index": index,
        "path": path,
        "start_line": 1,
        "end_line": 2,
        "text": f"text {index}",
        "score": 0.9 - index * 0.01,
    }


class FakeClient:
    def __init__(self, hits: list[dict], elapsed_ms: float = 12.5) -> None:
        self.hits = hits
        self.elapsed_ms = elapsed_ms
        self.calls: list[tuple[str, int]] = []

    def query(self, question: str, k: int) -> tuple[list[dict], float]:
        self.calls.append((question, k))
        return list(self.hits), self.elapsed_ms


def write_serve_log(path: Path, question: str, *, search_ms: int | None = 412, rerank: str | None = None) -> None:
    q = serve_log.question_hash(question)
    lines = []
    if search_ms is not None:
        lines.append(f"search q={q} k=20 candidates=30 duration_ms={search_ms}")
    if rerank is not None:
        lines.append(rerank.replace("{q}", q))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class SweepTest(unittest.TestCase):
    def question(self, text: str = "What is it?") -> Question:
        return Question(
            id="q1", question=text, applies_to=("single-project",), gold=()
        )

    def run_sweep(self, tmp: Path, questions: list[Question], rerank: str | None = None):
        path = tmp / "serve.log"
        write_serve_log(path, questions[0].question, rerank=rerank)
        client = FakeClient([hit(0), hit(1), hit(2), hit(3), hit(4)])
        options = RunOptions(repo_root=tmp, k_values=(3, 7))
        result = _sweep(client, questions, profile("single-project"), options, path)
        return client, result, options

    def test_one_query_per_question_at_max_k(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, result, _ = self.run_sweep(Path(tmp), [self.question()])

        self.assertEqual(client.calls, [("What is it?", 7)])
        self.assertEqual(result["questions"][0]["by_k"]["3"]["hits_returned"], 3)
        self.assertEqual(result["questions"][0]["by_k"]["7"]["hits_returned"], 5)

    def test_prefix_truncation_keeps_the_hit_records_per_k(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, result, options = self.run_sweep(Path(tmp), [self.question()])

        counts = {}
        for record in options.hits:
            counts[record["k"]] = counts.get(record["k"], 0) + 1
        self.assertEqual(counts, {3: 3, 7: 5})
        self.assertEqual(result["questions"][0]["answerable"], False)

    def test_hash_join_attaches_search_and_rerank_timings(self):
        rerank = (
            "rerank q={q} model=rerank-3 docs=5 results=5 duration_ms=1188 "
            "status=success tokens=39 cost=0.000351 cost_rub=0.03"
        )
        with tempfile.TemporaryDirectory() as tmp:
            _, result, _ = self.run_sweep(Path(tmp), [self.question()], rerank=rerank)

        timings = result["questions"][0]["timings"]
        self.assertEqual(timings["search_ms"], 412)
        self.assertEqual(timings["rerank_ms"], 1188)
        self.assertEqual(timings["e2e_ms"], 12.5)

        rerank_entry = result["questions"][0]["rerank"]
        self.assertEqual(rerank_entry["status"], "success")
        self.assertEqual(rerank_entry["docs"], 5)
        self.assertEqual(rerank_entry["tokens"], 39)

        profile_timings = result["timings"]
        self.assertEqual(profile_timings["search"]["p50_ms"], 412)
        self.assertEqual(profile_timings["rerank"]["p50_ms"], 1188)
        self.assertEqual(profile_timings["e2e"]["p50_ms"], 12.5)

    def test_failed_rerank_is_recorded_without_changing_quality(self):
        rerank = (
            "rerank q={q} model=rerank-3 docs=5 results=0 duration_ms=2001 "
            "status=failed error=rerank endpoint returned 500: boom"
        )
        with tempfile.TemporaryDirectory() as tmp:
            _, result, _ = self.run_sweep(Path(tmp), [self.question()], rerank=rerank)

        question = result["questions"][0]
        self.assertEqual(question["rerank"]["status"], "failed")
        self.assertEqual(question["rerank"]["results"], 0)
        self.assertEqual(question["timings"]["rerank_ms"], 2001)
        self.assertEqual(question["by_k"]["7"]["hits_returned"], 5)

    def test_missing_rerank_line_leaves_rerank_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, result, _ = self.run_sweep(Path(tmp), [self.question()], rerank=None)

        self.assertEqual(result["questions"][0]["timings"]["search_ms"], 412)
        self.assertIsNone(result["questions"][0]["timings"]["rerank_ms"])
        self.assertIsNone(result["questions"][0]["rerank"])
        self.assertEqual(result["timings"]["rerank"]["samples_ms"], [])

    def test_duplicate_question_text_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "serve.log"
            write_serve_log(path, "same")
            client = FakeClient([hit(0)])
            options = RunOptions(repo_root=Path(tmp), k_values=(3,))
            questions = [self.question("same"), self.question("same")]
            with self.assertRaisesRegex(RuntimeError, "not unique"):
                _sweep(client, questions, profile("single-project"), options, path)


if __name__ == "__main__":
    unittest.main()

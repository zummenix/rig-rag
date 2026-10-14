import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from eval.questions import Question
from eval.runner.profiles import Profile
from eval.runner.run import (
    _environment,
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


if __name__ == "__main__":
    unittest.main()

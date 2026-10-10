import unittest
from pathlib import Path

from eval.runner.run import load_run_inputs

REPO_ROOT = Path(__file__).resolve().parents[2]


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


if __name__ == "__main__":
    unittest.main()

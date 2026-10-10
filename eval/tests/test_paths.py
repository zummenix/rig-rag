import unittest
from pathlib import Path

from eval.runner.paths import repo_relative


class RepoRelativeTest(unittest.TestCase):
    def test_under_root_is_made_relative(self):
        self.assertEqual(
            repo_relative(Path("/repo/eval/profiles/p/rig-rag.toml"), Path("/repo")),
            Path("eval/profiles/p/rig-rag.toml"),
        )

    def test_outside_root_is_unchanged(self):
        self.assertEqual(
            repo_relative(Path("/opt/bin/rig-rag"), Path("/repo")),
            Path("/opt/bin/rig-rag"),
        )

    def test_relative_input_is_unchanged(self):
        self.assertEqual(
            repo_relative(Path("target/release/rig-rag"), Path("/repo")),
            Path("target/release/rig-rag"),
        )


if __name__ == "__main__":
    unittest.main()

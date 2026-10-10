import tempfile
import unittest
from pathlib import Path

from eval.runner.profiles import parse_profile
from eval.runner.rigrag import RigRag, resolve_binary


class ResolveBinaryTest(unittest.TestCase):
    def test_override_must_exist(self):
        with self.assertRaises(FileNotFoundError):
            resolve_binary(Path("/nonexistent"), override="/no/such/binary")

        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "rig-rag"
            binary.write_text("#!/bin/sh\n")
            self.assertEqual(resolve_binary(Path(tmp), override=str(binary)), binary.resolve())


class RigRagCommandTest(unittest.TestCase):
    def test_global_flags_precede_the_subcommand(self):
        rig = RigRag(Path("/bin/rig-rag"), Path("."))
        self.assertEqual(
            rig.serve_command(Path("cfg.toml"), "127.0.0.1:9", Path("no-site")),
            [
                "/bin/rig-rag",
                "--config",
                "cfg.toml",
                "serve",
                "--bind",
                "127.0.0.1:9",
                "--site-dir",
                "no-site",
            ],
        )


class ParseProfileTest(unittest.TestCase):
    def test_parses_corpus_and_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "rig-rag.toml").write_text(
                '[qdrant]\nurl = "http://x"\n\n[embedding]\nmodel = "m"\n\n'
                '[collection]\nactive = "eval-x-m-h"\n\n[corpus]\n'
                'prefix = "eval-x"\ndata_root = "data/x"\n'
            )
            (base / "sources.json").write_text(
                '[{"name": "jj", "type": "git", "url": "u"}]'
            )
            profile = parse_profile("x", base / "rig-rag.toml", base / "sources.json")
            self.assertEqual(profile.data_root, "data/x")
            self.assertEqual(profile.prefix, "eval-x")
            self.assertEqual(profile.active_collection, "eval-x-m-h")
            self.assertEqual(profile.source_names(), {"jj"})


if __name__ == "__main__":
    unittest.main()

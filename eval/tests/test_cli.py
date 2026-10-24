import argparse
import contextlib
import io
import unittest

from eval.runner import __main__ as cli
from eval.runner.metrics import K_VALUES


@contextlib.contextmanager
def quiet_stderr():
    """argparse prints usage to stderr on error; keep the test output clean."""

    with contextlib.redirect_stderr(io.StringIO()):
        yield


class ParseKTest(unittest.TestCase):
    def test_normalizes_sorts_and_dedupes(self):
        self.assertEqual(cli._parse_k("7, 3 ,20,3"), (3, 7, 20))

    def test_accepts_a_subset_of_the_contract_grid(self):
        self.assertEqual(cli._parse_k("3,20"), (3, 20))

    def test_rejects_values_outside_the_grid(self):
        for raw in ("1", "5", "10", "3,4", "0", "21"):
            with quiet_stderr(), self.assertRaises(argparse.ArgumentTypeError):
                cli._parse_k(raw)

    def test_rejects_non_integer(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            cli._parse_k("x")

    def test_rejects_empty(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            cli._parse_k(" , ")


class ParserTest(unittest.TestCase):
    def test_defaults_to_the_full_grid(self):
        args = cli.build_parser().parse_args([])
        self.assertEqual(args.k, K_VALUES)
        self.assertEqual(K_VALUES, (3, 7, 20))

    def test_removed_flags_are_rejected(self):
        for flag in ("--cache-rerank", "--warmup", "--reps", "--latency-k"):
            with quiet_stderr(), self.assertRaises(SystemExit):
                cli.build_parser().parse_args([flag, "1"] if flag in ("--warmup", "--reps", "--latency-k") else [flag])

    def test_parser_rejects_out_of_grid_k(self):
        with quiet_stderr(), self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["--k", "5"])

    def test_parser_accepts_subset_grid_k(self):
        args = cli.build_parser().parse_args(["--k", "3,20"])
        self.assertEqual(args.k, (3, 20))

    def test_modes_default_to_the_full_canonical_sweep(self):
        self.assertEqual(
            cli.build_parser().parse_args([]).modes,
            ("hybrid", "dense", "sparse"),
        )

    def test_parser_accepts_a_canonicalized_subset(self):
        args = cli.build_parser().parse_args(["--modes", "sparse, hybrid"])
        self.assertEqual(args.modes, ("hybrid", "sparse"))
        args = cli.build_parser().parse_args(["--modes", "dense"])
        self.assertEqual(args.modes, ("dense",))

    def test_parser_rejects_unknown_empty_and_duplicate_modes(self):
        for raw in ("fuzzy", " , ", "dense,dense", "hybrid,sparse,fuzzy"):
            with quiet_stderr(), self.assertRaises(SystemExit):
                cli.build_parser().parse_args(["--modes", raw])

    def test_removed_mode_flag_is_rejected(self):
        with quiet_stderr(), self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["--mode", "hybrid"])


if __name__ == "__main__":
    unittest.main()

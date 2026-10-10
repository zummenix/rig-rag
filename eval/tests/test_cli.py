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
        self.assertEqual(cli._parse_k("5, 1 ,3,1"), (1, 3, 5))

    def test_accepts_a_subset_of_the_contract_grid(self):
        self.assertEqual(cli._parse_k("1"), (1,))

    def test_rejects_values_outside_the_grid(self):
        for raw in ("4", "1,21", "0", "1,3,4"):
            with quiet_stderr(), self.assertRaises(argparse.ArgumentTypeError):
                cli._parse_k(raw)

    def test_rejects_non_integer(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            cli._parse_k("x")

    def test_rejects_empty(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            cli._parse_k(" , ")


class ParserTest(unittest.TestCase):
    def test_defaults_to_the_full_grid_at_k7(self):
        args = cli.build_parser().parse_args([])
        self.assertEqual(args.k, K_VALUES)
        self.assertEqual(args.latency_k, 7)

    def test_parser_rejects_out_of_grid_k(self):
        with quiet_stderr(), self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["--k", "4"])

    def test_parser_accepts_subset_grid_k(self):
        args = cli.build_parser().parse_args(["--k", "1,5"])
        self.assertEqual(args.k, (1, 5))


class LatencySelectionTest(unittest.TestCase):
    def test_main_rejects_latency_k_outside_the_sweep(self):
        # Fails during validation, before any ingest/serve work.
        with quiet_stderr(), self.assertRaises(SystemExit):
            cli.main(["--k", "1,3,5", "--latency-k", "7"])


if __name__ == "__main__":
    unittest.main()

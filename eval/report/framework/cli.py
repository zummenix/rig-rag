"""A tiny CLI shared by the thin per-experiment report scripts.

Each experiment ships a few-line `report.py` that calls `report_main`. Keeping
the argument parsing here means an experiment only declares its id and its
narrative notes, and every experiment's report behaves the same way.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from eval.report.framework import latest_results, load_results, render_report
from eval.runner.paths import repo_relative

# eval/report/framework/cli.py -> repository root
_REPO_ROOT = Path(__file__).resolve().parents[3]


def build_parser(experiment: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"python3 -m eval.experiments.{experiment}.report",
        description=f"Render the `{experiment}` experiment's results into a self-contained HTML report.",
    )
    parser.add_argument(
        "--results",
        default=None,
        help=(
            "results.json to render "
            f"(default: the newest eval/results/{experiment}/<run>/results.json)"
        ),
    )
    parser.add_argument(
        "--out",
        default=None,
        help=f"output HTML path (default: eval/reports/{experiment}.html)",
    )
    return parser


def report_main(
    *,
    experiment: str,
    notes: tuple[str, ...] = (),
    repo_root: str | Path | None = None,
    argv: list[str] | None = None,
) -> int:
    root = Path(repo_root).resolve() if repo_root else _REPO_ROOT
    args = build_parser(experiment).parse_args(argv)

    results_path = Path(args.results) if args.results else latest_results(
        root / "eval" / "results" / experiment
    )
    out_path = Path(args.out) if args.out else root / "eval" / "reports" / f"{experiment}.html"

    results = load_results(results_path)
    html = render_report(
        results,
        notes=notes,
        results_path=repo_relative(results_path, root),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"wrote {out_path} ({len(html)} bytes) from {results_path}")
    return 0

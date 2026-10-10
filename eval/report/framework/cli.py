"""A tiny CLI shared by the thin per-experiment report scripts.

Each experiment ships a few-line `report.py` that calls `report_main`. Keeping
the argument parsing here means an experiment only declares its id and its
narrative notes, and every experiment's report behaves the same way.

A run directory may hold a retrieval part (`results.json`), one ingestion part
per profile (`ingest-<profile>.json`), both, or neither. By default the newest
run that holds any part is rendered with whichever parts it contains; the flags
below select parts explicitly (e.g. `--no-retrieval` for an ingest-only report).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from eval.report.framework import compare, ingest as ingest_contract, loader
from eval.report.framework.parts import discover_run_parts, latest_run, resolve_run
from eval.report.framework.render import render_report
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
            "results.json to render (default: the newest "
            f"eval/results/{experiment}/<run>/ that holds a part)"
        ),
    )
    parser.add_argument(
        "--ingest",
        action="append",
        default=None,
        metavar="PATH",
        help=(
            "ingest report to include; repeatable "
            "(default: every ingest-*.json in the selected run directory)"
        ),
    )
    parser.add_argument(
        "--no-retrieval",
        action="store_true",
        help="omit the retrieval part (results.json)",
    )
    parser.add_argument(
        "--no-ingest",
        action="store_true",
        help="omit the ingestion part(s) (ingest-*.json)",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        metavar="ID_OR_PATH",
        help=(
            "run to diff against: an experiment id (its newest run) or a path "
            "(run directory or results.json); default: the experiment's declared baseline"
        ),
    )
    parser.add_argument(
        "--no-baseline",
        action="store_true",
        help="render without the comparison section even if a baseline is declared",
    )
    parser.add_argument(
        "--commit",
        default=None,
        help=(
            "measured commit for a report without a retrieval part "
            "(defaults to the sibling results.json's commit)"
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
    baseline: str | None = None,
    repo_root: str | Path | None = None,
    argv: list[str] | None = None,
) -> int:
    root = Path(repo_root).resolve() if repo_root else _REPO_ROOT
    args = build_parser(experiment).parse_args(argv)

    if args.no_retrieval and args.no_ingest:
        raise loader.ResultsError("--no-retrieval and --no-ingest leave nothing to render")

    run_dir: Path | None = None
    results_path: Path | None = None
    if args.results:
        results_path = Path(args.results)
        run_dir = results_path.parent
    else:
        parts = latest_run(root / "eval" / "results" / experiment)
        run_dir = parts.run_dir
        results_path = parts.results

    if args.ingest:
        ingest_paths = [Path(path) for path in args.ingest]
    elif run_dir is not None:
        ingest_paths = list(discover_run_parts(run_dir).ingest)
    else:
        ingest_paths = []

    if args.no_ingest:
        ingest_paths = []

    # Load the retrieval part before applying --no-retrieval: even when its
    # section is suppressed, its sibling results.json declares the measured
    # commit the ingest report shows. An explicit --commit overrides it.
    results = loader.load_results(results_path) if results_path is not None else None
    commit = args.commit or ((results or {}).get("commit") or None)

    if args.no_retrieval:
        results_path = None
        results = None

    if results is None and not ingest_paths:
        raise loader.ResultsError(
            f"no report parts to render for {experiment!r} "
            "(no results.json and no ingest-*.json selected)"
        )

    ingest_docs = [ingest_contract.load_ingest(path) for path in ingest_paths]

    baseline_ref = None if args.no_baseline else (args.baseline or baseline)
    comparison = None
    if baseline_ref is not None:
        loaded = resolve_run(baseline_ref, root=root)
        comparison = compare.Baseline(
            label=loaded.label,
            results=loaded.results,
            ingest=loaded.ingest_documents(),
        )

    out_path = Path(args.out) if args.out else root / "eval" / "reports" / f"{experiment}.html"
    html = render_report(
        results,
        ingest=ingest_docs,
        baseline=comparison,
        notes=notes,
        results_path=repo_relative(results_path, root) if results_path is not None else None,
        ingest_paths=[repo_relative(path, root) for path in ingest_paths],
        commit=commit,
        experiment=experiment,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    against = f" vs {comparison.label}" if comparison is not None else ""
    print(f"wrote {out_path} ({len(html.encode('utf-8'))} bytes){against}")
    return 0

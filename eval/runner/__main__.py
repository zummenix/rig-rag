"""Command-line entry point: `python3 -m eval.runner`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from eval.runner.metrics import K_VALUES, LATENCY_K
from eval.runner.results import validate_metric_k
from eval.runner.run import RunOptions, run


def _parse_k(raw: str) -> tuple[int, ...]:
    values: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not an integer: {part!r}") from None
        if value not in K_VALUES:
            raise argparse.ArgumentTypeError(
                f"k must be one of {','.join(map(str, K_VALUES))}, got {value}"
            )
        values.add(value)
    if not values:
        raise argparse.ArgumentTypeError("at least one k value is required")
    return tuple(sorted(values))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m eval.runner",
        description="Run the retrieval eval against corpus profiles and write results JSON.",
    )
    parser.add_argument(
        "--experiment",
        default="baseline",
        help="experiment id used in eval/results/<id>/ (default: baseline)",
    )
    parser.add_argument(
        "--profiles",
        default=None,
        help="comma-separated profile names (default: every profile under eval/profiles)",
    )
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="reuse each profile's active collection instead of re-ingesting",
    )
    parser.add_argument(
        "--force-ingest",
        action="store_true",
        help="rebuild the target collection even when it already exists",
    )
    parser.add_argument(
        "--bin",
        default=None,
        help="path to the rig-rag binary (default: target/{release,debug}/rig-rag)",
    )
    parser.add_argument(
        "--k",
        type=_parse_k,
        default=K_VALUES,
        help=(
            "comma-separated subset of the contract k grid "
            f"({','.join(map(str, K_VALUES))}); default: all"
        ),
    )
    parser.add_argument("--warmup", type=int, default=3, help="latency warmup calls per question")
    parser.add_argument("--reps", type=int, default=5, help="latency repetitions per question")
    parser.add_argument(
        "--latency-k",
        type=int,
        default=LATENCY_K,
        help=(
            f"k used for latency measurement (default: {LATENCY_K}); "
            "must be one of --k"
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="fixed server port (default: 0 = pick a free port)",
    )
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=90.0,
        help="seconds to wait for /api/health after starting serve",
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="repository root (default: current working directory)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    # Keep our progress lines interleaved correctly with the subprocess output
    # when stdout is a pipe (e.g. `| tail`), which otherwise block-buffers.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        k_values = validate_metric_k(args.k, args.latency_k)
    except ValueError as error:
        parser.error(str(error))

    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path.cwd().resolve()
    selected = (
        [name.strip() for name in args.profiles.split(",") if name.strip()]
        if args.profiles
        else None
    )
    options = RunOptions(
        repo_root=repo_root,
        experiment=args.experiment,
        profiles=selected,
        skip_ingest=args.skip_ingest,
        force_ingest=args.force_ingest,
        binary=args.bin,
        k_values=k_values,
        warmup=args.warmup,
        reps=args.reps,
        latency_k=args.latency_k,
        port=args.port,
        startup_timeout=args.startup_timeout,
    )
    run(options)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Experiment orchestration: ingest -> promote -> serve -> k-sweep -> results."""

from __future__ import annotations

import platform
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from eval.questions import (
    QUESTIONS_PATH,
    Question,
    applicable_questions,
    load_questions,
    validate_questions,
)
from eval.runner import results as results_mod
from eval.runner.httpquery import QueryClient
from eval.runner.metrics import K_VALUES, evaluate_at_k, mean, percentile
from eval.runner.gold import source_of
from eval.runner.paths import repo_relative
from eval.runner.profiles import Profile, discover_profiles, parse_profile
from eval.runner.rigrag import RigRag, binary_sha256, resolve_binary
from eval.runner.serve import free_port, running_server
from eval.runner import serve_log

RESULTS_DIR = "eval/results"


@dataclass
class RunOptions:
    repo_root: Path
    experiment: str = "baseline"
    profiles: list[str] | None = None
    skip_ingest: bool = False
    force_ingest: bool = False
    binary: str | None = None
    k_values: tuple[int, ...] = K_VALUES
    modes: tuple[str, ...] = results_mod.SEARCH_MODES
    port: int = 0
    startup_timeout: float = 90.0
    hits: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Canonicalize here too so programmatic callers (tests, experiments)
        # cannot sweep in a different order than the CLI/report contract.
        self.modes = results_mod.validate_modes(self.modes)


def _git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _git_optional(repo_root: Path, *args: str) -> str | None:
    """Best-effort `git`; `None` when git is unavailable or the command fails."""

    try:
        return _git(repo_root, *args)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def measured_revision(repo_root: Path) -> tuple[str, str]:
    """Best-effort `(commit, remote_ref)`; never fails on repository state.

    The runner measures whatever the work tree holds, so jj working-copy
    commits, a detached `HEAD`, and a dirty tree are all fine. `commit` is the
    git revision when one can be read; `remote_ref` is the pushed ref containing
    it, or `""`. Both are a breadcrumb, not a contract: the record's
    `## Reproduction` is the human-facing provenance.
    """

    commit = _git_optional(repo_root, "rev-parse", "HEAD") or ""
    if not commit:
        return "", ""
    raw = _git_optional(
        repo_root,
        "for-each-ref",
        "--contains",
        commit,
        "--format=%(refname)",
        "refs/remotes",
    )
    refs = [ref for ref in (raw or "").splitlines() if ref and not ref.endswith("/HEAD")]
    return commit, refs[0].removeprefix("refs/remotes/") if refs else ""


def uncommitted_tracked_changes(repo_root: Path) -> list[str]:
    """Tracked paths with uncommitted changes, for the run-time breadcrumb.

    Uses `git diff HEAD --name-only` (staged + unstaged, untracked excluded)
    rather than `git status --porcelain`: the status format's leading field is
    whitespace-sensitive, which a stripped command output would corrupt.
    """

    names = _git_optional(repo_root, "diff", "HEAD", "--name-only")
    if not names:
        return []
    return [line for line in names.splitlines() if line]


def _environment(binary: Path, qdrant_url: str, repo_root: Path) -> dict:
    return {
        "os": sys.platform,
        "arch": platform.machine(),
        "python": platform.python_version(),
        "binary": str(repo_relative(binary, repo_root)),
        "binary_sha256": binary_sha256(binary),
        "qdrant_url": qdrant_url,
    }


def _hit_record(
    profile: str,
    mode: str,
    question: str,
    k: int,
    rank: int,
    hit: dict,
    data_root: str,
) -> dict:
    return {
        "profile": profile,
        "mode": mode,
        "question": question,
        "k": k,
        "rank": rank,
        "score": hit["score"],
        "source": source_of(hit["path"], data_root),
        "path": hit["path"],
        "start_line": hit["start_line"],
        "end_line": hit["end_line"],
        "chunk_index": hit["chunk_index"],
        "text": hit["text"],
    }


def _timing_summary(values: list[float | None]) -> dict:
    """Percentiles across questions (one sample per question, not per trial)."""

    present = [float(value) for value in values if value is not None]
    return {
        "samples_ms": present,
        "p50_ms": percentile(present, 50),
        "p95_ms": percentile(present, 95),
        "mean_ms": mean(present),
        "sum_ms": sum(present) if present else None,
    }


def _attach_log_metrics(per_question: list[dict], records: dict) -> None:
    """Joins the serve log's per-(hash, mode) search/rerank lines onto the questions."""

    for record in per_question:
        question_hash = serve_log.question_hash(record["question"])
        by_mode = records.get(question_hash) or {}
        for mode, mode_record in record["modes"].items():
            entry = by_mode.get(mode) or {}
            search = entry.get("search") or {}
            rerank = entry.get("rerank") or {}
            mode_record["timings"]["search_ms"] = search.get("duration_ms")
            mode_record["timings"]["rerank_ms"] = rerank.get("duration_ms")
            mode_record["rerank"] = (
                {
                    "docs": rerank.get("docs"),
                    "results": rerank.get("results"),
                    "tokens": rerank.get("tokens"),
                    "cost": rerank.get("cost"),
                    "cost_rub": rerank.get("cost_rub"),
                    "status": rerank.get("status"),
                }
                if rerank
                else None
            )


def _sweep(
    client: QueryClient,
    questions: list[Question],
    profile: Profile,
    options: RunOptions,
    log_path: Path,
) -> dict:
    """One uncached query per (question, mode) at `max(k)`, then prefix truncation.

    Every question is measured once per requested mode, in canonical mode order.
    With `[reranker].candidates >= max(k)` (the eval profiles pin a pool at
    least as large as `max(k)`), every smaller-k ranked list is an exact prefix
    of that mode's `max(k)` list, so truncating the returned hits is equivalent
    to querying at each k. Only the max-k list's hit locations are stored; the
    smaller-k lists are derivable prefixes.
    """

    query_k = max(options.k_values)
    by_hash = {serve_log.question_hash(question.question): question for question in questions}
    if len(by_hash) != len(questions):
        raise RuntimeError(
            f"profile questions are not unique by text in {log_path.parent.name!r}; "
            "hash correlation would collapse their log records"
        )

    per_question: list[dict] = [
        {
            "id": question.id,
            "question": question.question,
            "answerable": question.answerable,
            "gold": results_mod.gold_entry(question),
            "notes": question.notes,
            "modes": {},
        }
        for question in questions
    ]
    by_id = {record["id"]: record for record in per_question}

    for mode in options.modes:
        print(f"  mode {mode}: {len(questions)} question(s)")
        for question in questions:
            hits, elapsed_ms = client.query(question.question, query_k, mode)
            by_k = {
                str(k): evaluate_at_k(hits[:k], question, profile.data_root)
                for k in options.k_values
            }
            for k in options.k_values:
                if k != query_k:
                    by_k[str(k)].pop("hits", None)
                for rank, hit in enumerate(hits[:k], start=1):
                    options.hits.append(
                        _hit_record(
                            profile.name,
                            mode,
                            question.id,
                            k,
                            rank,
                            hit,
                            profile.data_root,
                        )
                    )
            by_id[question.id]["modes"][mode] = {
                "by_k": by_k,
                "timings": {
                    "e2e_ms": round(elapsed_ms, 3),
                    "search_ms": None,
                    "rerank_ms": None,
                },
                "rerank": None,
            }

    # Read the log only after the sweep: a real `serve` appends each line while
    # the query runs, so parsing any earlier attaches a stale (usually empty)
    # snapshot and every timing/rerank rollup comes out null.
    records = serve_log.parse_log(log_path)
    _attach_log_metrics(per_question, records)

    modes: dict = {}
    for mode in options.modes:
        mode_questions = [record["modes"][mode] for record in per_question]
        modes[mode] = {
            "timings": {
                "search": _timing_summary(
                    [q["timings"]["search_ms"] for q in mode_questions]
                ),
                "rerank": _timing_summary(
                    [q["timings"]["rerank_ms"] for q in mode_questions]
                ),
                "e2e": _timing_summary(
                    [q["timings"]["e2e_ms"] for q in mode_questions]
                ),
            },
            "rerank": serve_log.rerank_stats(records, mode),
        }

    return {
        "prefix": profile.prefix,
        "data_root": profile.data_root,
        "source_names": sorted(profile.source_names()),
        "modes": modes,
        "questions": per_question,
    }


def require_applicable_questions(profiles: list[Profile], questions: list[Question]) -> None:
    """Fails when a selected profile has no questions to evaluate.

    An empty applicable set would otherwise yield a vacuous `results.json` (no
    per-question metrics, `None` latency percentiles) reported as a success.
    """

    for profile in profiles:
        if not applicable_questions(questions, profile.name):
            raise ValueError(
                f"profile {profile.name!r} has no applicable questions; "
                "add a question applying to it or drop the profile"
            )


def load_run_inputs(repo_root: Path, selected: list[str] | None) -> tuple[list[Profile], list[Question]]:
    """Selects the profiles to run and validates the question set.

    Validation uses **every** profile on disk, not just the selected subset, so
    a single-profile run still checks questions that reference other profiles.
    """

    profiles = discover_profiles(repo_root, selected)
    questions = load_questions(repo_root / QUESTIONS_PATH)
    all_profiles = discover_profiles(repo_root, None)
    validate_questions(questions, {p.name: p.source_names() for p in all_profiles})
    require_applicable_questions(profiles, questions)
    return profiles, questions


def run(options: RunOptions) -> Path:
    repo_root = Path(options.repo_root)
    commit, commit_ref = measured_revision(repo_root)
    run_id = results_mod.utc_run_id()
    started_at = results_mod.utc_timestamp()
    run_dir = repo_root / RESULTS_DIR / options.experiment / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    binary = resolve_binary(repo_root, options.binary)
    rig = RigRag(binary, repo_root)
    print(f"rig-rag binary: {binary}")
    print(f"commit: {commit or '<none>'} ({commit_ref or 'no pushed ref'})")
    print(f"search modes: {', '.join(options.modes)}")
    changed = uncommitted_tracked_changes(repo_root)
    if changed:
        print(
            f"warning: {len(changed)} uncommitted tracked path(s) at run time; the "
            "recorded commit does not capture them:"
        )
        for path in changed[:10]:
            print(f"  {path}")
        if len(changed) > 10:
            print(f"  … and {len(changed) - 10} more")
    print(f"run directory: {run_dir}")

    profiles, questions = load_run_inputs(repo_root, options.profiles)

    config_snapshot: dict = {}
    profile_results: dict = {}

    for profile in profiles:
        applicable = applicable_questions(questions, profile.name)
        print(f"\n=== profile {profile.name}: {len(applicable)} question(s) ===")

        if options.skip_ingest:
            collection = profile.active_collection
            if not collection:
                raise RuntimeError(
                    f"--skip-ingest needs [collection].active set in {profile.config_path}"
                )
            print(f"reusing active collection {collection}")
        else:
            report_path = run_dir / f"ingest-{profile.name}.json"
            report = rig.ingest(
                profile.config_path,
                profile.sources_path,
                report_path,
                force=options.force_ingest,
            )
            collection = report["collection"]
            print(f"ingested {collection} ({report['status']})")

        rig.promote(profile.config_path, collection)
        # `promote` wrote the active collection; snapshot the files as used.
        profile = parse_profile(
            profile.name, profile.config_path, profile.sources_path
        )
        config_snapshot[profile.name] = results_mod.config_snapshot(
            profile.config, profile.sources
        )

        port = options.port or free_port()
        log_path = run_dir / f"serve-{profile.name}.log"
        with running_server(
            rig,
            profile.config_path,
            port,
            log_path,
            startup_timeout=options.startup_timeout,
        ) as client:
            result = _sweep(client, applicable, profile, options, log_path)
        profile_results[profile.name] = {"collection": collection, **result}

    qdrant_url = profiles[0].config.get("qdrant", {}).get("url", "")
    results = results_mod.build_results(
        experiment=options.experiment,
        run=run_id,
        started_at=started_at,
        commit=commit,
        commit_ref=commit_ref,
        config=config_snapshot,
        environment=_environment(binary, qdrant_url, repo_root),
        profiles=profile_results,
        modes=options.modes,
        k_values=options.k_values,
    )

    hits_path = results_mod.write_hits(run_dir, options.hits)
    results_path = results_mod.write_results(run_dir, results)
    print(f"\nwrote {results_path}")
    print(f"wrote {hits_path}")
    trailer = f"Eval-Experiment: {options.experiment}"
    if commit:
        trailer += f"\nEval-Commit: {commit}"
    print(f"\nsuggested report-commit trailer:\n{trailer}")
    return run_dir

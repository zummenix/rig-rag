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
from eval.runner.metrics import K_VALUES, LATENCY_K, evaluate_at_k, percentile
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
    warmup: int = 3
    reps: int = 5
    latency_k: int = LATENCY_K
    cache_rerank: bool = False
    port: int = 0
    startup_timeout: float = 90.0
    hits: list[dict] = field(default_factory=list)


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


def _hit_record(profile: str, question: str, k: int, rank: int, hit: dict, data_root: str) -> dict:
    return {
        "profile": profile,
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


def _sweep(
    client: QueryClient,
    questions: list[Question],
    profile: Profile,
    options: RunOptions,
) -> dict:
    per_question: list[dict] = []
    latency_samples: list[float] = []
    cold_samples: list[float] = []

    for question in questions:
        by_k: dict[str, dict] = {}
        cold_ms: float | None = None
        for index, k in enumerate(options.k_values):
            hits, elapsed_ms = client.query(question.question, k)
            if index == 0 and options.cache_rerank:
                # The first retrieval query for a question is the only cache
                # miss; later k values reuse the same candidate-set ranking.
                cold_ms = round(elapsed_ms, 3)
                cold_samples.append(cold_ms)
            by_k[str(k)] = evaluate_at_k(hits, question, profile.data_root)
            for rank, hit in enumerate(hits, start=1):
                options.hits.append(
                    _hit_record(profile.name, question.id, k, rank, hit, profile.data_root)
                )

        for _ in range(options.warmup):
            client.query(question.question, options.latency_k)
        latency_ms = []
        for _ in range(options.reps):
            _, elapsed_ms = client.query(question.question, options.latency_k)
            latency_ms.append(round(elapsed_ms, 3))
        latency_samples.extend(latency_ms)

        per_question.append(
            {
                "id": question.id,
                "question": question.question,
                "answerable": question.answerable,
                "gold": results_mod.gold_entry(question),
                "notes": question.notes,
                "by_k": by_k,
                "latency_ms": latency_ms,
                "cold_ms": cold_ms,
            }
        )

    latency = {
        "warmup": options.warmup,
        "reps": options.reps,
        "latency_k": options.latency_k,
        "cache_rerank": options.cache_rerank,
        "samples_ms": latency_samples,
        "p50_ms": percentile(latency_samples, 50),
        "p95_ms": percentile(latency_samples, 95),
    }
    if cold_samples:
        latency["cold_samples_ms"] = cold_samples
        latency["cold_p50_ms"] = percentile(cold_samples, 50)
        latency["cold_p95_ms"] = percentile(cold_samples, 95)

    return {
        "prefix": profile.prefix,
        "data_root": profile.data_root,
        "source_names": sorted(profile.source_names()),
        "latency": latency,
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
            cache_rerank=options.cache_rerank,
        ) as client:
            result = _sweep(client, applicable, profile, options)
        result["collection"] = collection
        result["rerank"] = serve_log.rerank_stats(log_path)
        profile_results[profile.name] = result

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
        k_values=options.k_values,
        latency_k=options.latency_k,
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

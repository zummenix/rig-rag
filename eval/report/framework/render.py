"""Render committed report parts into one self-contained HTML document.

The retrieval part (`results.json`) and the ingestion part
(`ingest-<profile>.json`, one per profile) are independent, separately
versioned artifacts. This module composes whichever parts a run contains, so a
retrieval-only, ingest-only, or combined document all render. Every part is
validated by its own loader before anything is drawn.

The document is assembled from the smaller `layout` helpers and `svg` figures:
provenance, a mode×metric summary, a cross-mode comparison (for v3 runs with
more than one mode), retrieval quality (per profile and mode, with per-question
breakdowns collapsed behind `<details>`), timing (local search, paid rerank, and
end-to-end, per `profile · mode`), cross-corpus degradation, ingestion cost, and
the exact configuration the run was measured with. Nothing here reaches the
network.
"""

from __future__ import annotations

import json

from eval.report.framework import compare, ingest as ingest_contract, layout, loader, svg

DASH = "\u2013"  # en dash for an undefined value

# The report's chunk-token buckets, in display order. These are the serialized
# keys of the Rust report (see `src/report.rs`).
_BUCKET_LABELS = ("<50", "50-99", "100-199", "200-399", ">=400")


def _number(value: object, *, digits: int = 3) -> str:
    if value is None:
        return DASH
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    text = f"{number:.{digits}f}"
    if "." in text:
        # Trim trailing fractional zeros only. Stripping "0" unconditionally
        # would corrupt an integer formatted at 0 digits (e.g. "550" -> "55").
        text = text.rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _percent(value: object, *, digits: int = 1) -> str:
    if value is None:
        return DASH
    try:
        return f"{float(value) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return str(value)


def _bytes(value: object) -> str:
    if value is None:
        return DASH
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    for factor, unit in ((1 << 30, "GiB"), (1 << 20, "MiB"), (1 << 10, "KiB")):
        if number >= factor:
            return f"{number / factor:.2f} {unit}"
    return f"{int(number)} B"


def _short_sha(value: object) -> str:
    text = str(value or "")
    return text[:12] if len(text) > 12 else text


def _profile_color(name: str) -> str:
    if name == "single-project":
        return svg.PALETTE["single"]
    if name == "multi-project":
        return svg.PALETTE["multi"]
    return svg.PALETTE["p50"]


_MODE_COLORS = {
    "hybrid": svg.PALETTE["mode_hybrid"],
    "dense": svg.PALETTE["mode_dense"],
    "sparse": svg.PALETTE["mode_sparse"],
}


def _mode_color(mode: str) -> str:
    return _MODE_COLORS.get(mode, svg.PALETTE["mode_unknown"])


def _group_by_profile(entries: list[dict]) -> dict[str, list[dict]]:
    """Series entries grouped by profile, preserving their order."""

    grouped: dict[str, list[dict]] = {}
    for entry in entries:
        grouped.setdefault(str(entry.get("profile", "")), []).append(entry)
    return grouped


def _series_key(profile: str, mode: str) -> str:
    return f"{profile} · {mode}"


def _series_label(entry: dict) -> str:
    return _series_key(
        entry.get("profile", ""), entry.get("mode", loader.UNKNOWN_MODE)
    )


def _by_question(entry: dict) -> dict[str, dict]:
    return {str(q.get("id", "")): q for q in entry.get("questions") or []}


def _summary_metric(entry: dict, metric: str, k: int) -> float | None:
    summary = entry.get("summary") or {}
    if metric == "recall":
        return (summary.get("mean_recall") or {}).get(f"recall@{k}")
    if metric == "purity":
        return summary.get(f"mean_purity@{k}")
    if metric == "mrr":
        return summary.get(f"mean_mrr@{k}")
    return None


def _summary_timing(entry: dict, phase: str, stat: str) -> float | None:
    timings = (entry.get("summary") or {}).get("timings") or {}
    return (timings.get(phase) or {}).get(stat)


def _header(title: str, results: dict | None, subtitle: str | None) -> str:
    if subtitle is None:
        source = results or {}
        run = source.get("run", "")
        started = source.get("started_at", "")
        subtitle = " · ".join(part for part in (f"run {run}" if run else "", started) if part)
    return (
        f"<header><h1>{layout.esc(title)}</h1>"
        f'<p class="subtitle">{layout.esc(subtitle)}</p></header>'
    )


def _provenance(
    results: dict | None,
    k_values: tuple[int, ...],
    ingest: list[dict],
    commit: str | None,
) -> str:
    if results is not None:
        pairs: list[tuple[str, object]] = [
            ("Experiment", results.get("experiment", "")),
            ("Run", results.get("run", "")),
            ("Started", results.get("started_at", "")),
            ("Measured commit", results.get("commit", "")),
        ]
        if results.get("commit_ref"):
            pairs.append(("Contained by", results["commit_ref"]))
        modes = loader.recorded_modes(results)
        pairs.append(
            (
                "Vector search",
                ", ".join(modes) if modes else loader.UNKNOWN_MODE_LABEL,
            )
        )
        pairs.append(("Reported k", ", ".join(str(k) for k in k_values)))
        pairs.append(
            ("Queries", "one uncached query per question per mode at max(k)")
        )
        environment = results.get("environment") or {}
    else:
        pairs = []
        if commit:
            pairs.append(("Measured commit", commit))
        labels = ", ".join(ingest_contract.profile_label(doc) for doc in ingest)
        if labels:
            pairs.append(("Profiles", labels))
        environment = next(
            (doc.get("environment") for doc in ingest if doc.get("environment")), {}
        ) or {}

    known = ("os", "arch", "python", "binary", "binary_sha256", "qdrant_url")
    env_pairs = [(key, environment[key]) for key in known if key in environment]

    body = layout.kv_table(pairs) if pairs else ""
    if env_pairs:
        body += "<h3>Environment</h3>" + layout.kv_table(env_pairs)
    return layout.section("Provenance", layout.Raw(body), anchor="provenance")


def _notes(notes: list[str] | tuple[str, ...]) -> str:
    items = "".join(f"<li>{layout.esc(note)}</li>" for note in notes)
    return layout.section("Notes", layout.Raw(f'<ul class="notes">{items}</ul>'))


def _min_chunk_tokens(value: object) -> str:
    if value is None:
        return "0 (no filtering)"
    try:
        number = int(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number} (drop smaller)" if number > 0 else "0 (no filtering)"


def _ingestion(ingest: list[dict], ingest_paths: list[object]) -> str:
    blocks = []
    for index, document in enumerate(ingest):
        path = ingest_paths[index] if index < len(ingest_paths) else None
        blocks.append(_ingest_block(document, path))
    return layout.section("Ingestion", layout.Raw("".join(blocks)), anchor="ingestion")


def _ingest_block(document: dict, path: object) -> str:
    status = str(document.get("status", "unknown"))
    measured = ingest_contract.cost_measured(document)

    out = [f"<h3>{layout.esc(ingest_contract.profile_label(document))}</h3>"]
    badge = layout.badge(f"status: {status}")
    if measured:
        out.append(f'<div class="statusline">{badge}</div>')
    else:
        out.append(
            f'<div class="statusline">{badge}</div>'
            '<p class="warn">Cost not measured. The collection already existed '
            '(<code>status = reused</code>), so this run gathered no counts, tokens, or '
            "timings. Rebuild with <code>--force</code> (or a new collection) to measure "
            "real cost.</p>"
        )

    model = document.get("model") or {}
    pairs: list[tuple[str, object]] = [
        ("Profile", document.get("profile", "")),
        ("Collection", document.get("collection", "")),
        ("Prefix", document.get("prefix", "")),
        ("Model", f"{model.get('slug', '')} ({_number(model.get('dimensions'), digits=0)}d)"),
        ("Min chunk tokens", _min_chunk_tokens(document.get("min_chunk_tokens"))),
        ("Tokenizer", document.get("tokenizer") or DASH),
        ("Force rebuild", "yes" if document.get("force") else "no"),
        ("Config", document.get("config", "")),
        ("Sources", document.get("sources", "")),
    ]
    if path is not None:
        pairs.append(("Report", str(path)))
    out.append(layout.kv_table(pairs))

    if measured:
        out.append(_ingest_cost(document))
    return "".join(out)


def _ingest_cost(document: dict) -> str:
    timing = document.get("timing_ms") or {}
    phases = ("fetch", "load", "chunk", "embed", "insert", "total")
    timing_rows = [[phase, _number(timing.get(phase), digits=0)] for phase in phases]

    memory = document.get("memory") or {}
    totals = document.get("totals") or {}
    facts: list[tuple[str, object]] = [
        ("Documents", _number(totals.get("documents"))),
        ("Chunks embedded", _number(totals.get("chunks"))),
        ("Embeddings", _number(totals.get("embeddings"))),
        ("Tokens", _number(totals.get("tokens"))),
        ("Peak RSS", _bytes(memory.get("peak_rss_bytes"))),
        ("cgroup peak", _bytes(memory.get("cgroup_peak_bytes"))),
    ]

    source_rows: list[list[object]] = []
    bucket_totals = {label: 0 for label in _BUCKET_LABELS}
    for source in document.get("sources_detail") or []:
        distribution = source.get("chunk_tokens") or {}
        buckets = distribution.get("buckets") or {}
        for label in _BUCKET_LABELS:
            bucket_totals[label] += int(buckets.get(label, 0) or 0)
        source_rows.append(
            [
                source.get("name", ""),
                _short_sha(source.get("resolved_commit", "")),
                _number(source.get("documents")),
                _number(source.get("chunks")),
                _number(source.get("tokens")),
                _number(distribution.get("min")),
                _number(distribution.get("p50")),
                _number(distribution.get("p90")),
                _number(distribution.get("max")),
            ]
        )

    chart = svg.grouped_bar_chart(
        list(_BUCKET_LABELS),
        [
            svg.Series(
                "chunks",
                [bucket_totals[label] for label in _BUCKET_LABELS],
                svg.PALETTE["single"],
            )
        ],
        value_format="0.0f",
        show_values=True,
        y_label="chunks",
        title="Embedded chunk-size distribution (tokens)",
    )

    waterfall = svg.waterfall(
        [
            (phase, timing.get(phase))
            for phase in ("fetch", "load", "chunk", "embed", "insert")
            if timing.get(phase) is not None
        ],
        total=timing.get("total"),
        title="Ingest phase timeline (milliseconds)",
        width=700,
    )
    body = "<h4>Timing</h4>"
    body += layout.figure(
        layout.Raw(waterfall),
        "Measured phases in execution order: each bar begins where the previous "
        "one ended (width = duration, in milliseconds). Trailing space is "
        "wall-clock not attributed to a measured phase (collection setup, "
        "hashing, reporting).",
    )
    body += layout.table(["Phase", "ms"], timing_rows, numeric_from=1)
    body += "<h4>Cost</h4>" + layout.kv_table(facts)
    if source_rows:
        body += "<h4>Per source</h4>" + layout.table(
            ["Source", "Commit", "Documents", "Chunks", "Tokens", "min", "p50", "p90", "max"],
            source_rows,
            numeric_from=2,
        )
    body += "<h4>Chunk-token distribution</h4>"
    body += layout.figure(
        layout.Raw(chart), "Embedded chunk sizes, bucketed by token count."
    )
    body += layout.table(
        ["Bucket (tokens)", "Chunks"],
        [[label, _number(bucket_totals[label])] for label in _BUCKET_LABELS],
        numeric_from=1,
    )
    return body


def _summary(series: list[dict], k_values: tuple[int, ...]) -> str:
    blocks: list[str] = []
    for profile, entries in _group_by_profile(series).items():
        counts = entries[0].get("counts") or {}
        headers: list[object] = ["Mode"]
        headers += [f"recall@{k}" for k in k_values]
        headers += [f"purity@{k}" for k in k_values]
        headers += [f"MRR@{k}" for k in k_values]
        headers += ["no-hit", "e2e p50 ms", "e2e p95 ms"]

        rows: list[list[object]] = []
        for entry in entries:
            summary = entry.get("summary") or {}
            mean_recall = summary.get("mean_recall") or {}
            e2e = (summary.get("timings") or {}).get("e2e") or {}
            rows.append(
                [entry.get("mode", loader.UNKNOWN_MODE)]
                + [_number(mean_recall.get(f"recall@{k}")) for k in k_values]
                + [_number(summary.get(f"mean_purity@{k}")) for k in k_values]
                + [_number(summary.get(f"mean_mrr@{k}")) for k in k_values]
                + [
                    _percent(summary.get("unanswerable_no_hit_rate")),
                    _number(e2e.get("p50_ms"), digits=2),
                    _number(e2e.get("p95_ms"), digits=2),
                ]
            )

        blocks.append(f"<h3>{layout.esc(profile)}</h3>")
        blocks.append(
            layout.kv_table(
                [
                    ("Questions", _number(counts.get("questions_applied"))),
                    ("Answerable", _number(counts.get("answerable"))),
                    ("Unanswerable", _number(counts.get("unanswerable"))),
                ]
            )
        )
        blocks.append(layout.table(headers, rows, numeric_from=1))

    return layout.section("Summary", layout.Raw("".join(blocks)), anchor="summary")


def _modes_section(
    series: list[dict], k_values: tuple[int, ...], modes: tuple[str, ...]
) -> str:
    """Cross-mode comparison: one profile block per recorded profile.

    Only v3 runs with more than one measured mode get this section; a v2 or a
    single-mode run has nothing to compare and omits it entirely.
    """

    max_k = max(k_values)
    blocks: list[str] = [
        "<p>Every question ran once per mode in the same ingest/serve pass, so "
        "the question set and reranker configuration are identical across modes; "
        "quality differences come from the retrieval arm. Deltas are relative to "
        "<code>hybrid</code> where it was measured.</p>"
    ]

    for profile, entries in _group_by_profile(series).items():
        by_mode = {str(entry.get("mode")): entry for entry in entries}
        present = [mode for mode in modes if mode in by_mode]
        if not present:
            continue
        reference = "hybrid" if "hybrid" in present else present[0]

        blocks.append(f"<h3>{layout.esc(profile)}</h3>")
        chart = svg.line_chart(
            [str(k) for k in k_values],
            [
                svg.Series(
                    mode,
                    [_summary_metric(by_mode[mode], "recall", k) for k in k_values],
                    _mode_color(mode),
                    dashed=(mode != reference),
                )
                for mode in present
            ],
            y_min=0.0,
            y_max=1.0,
            x_label="k",
            y_label="mean recall@k",
            title=f"Mean recall@k by search mode — {profile}",
        )
        blocks.append(
            layout.figure(
                layout.Raw(chart),
                "Mean recall@k per search mode over the profile's applicable "
                "answerable questions (solid = reference mode).",
            )
        )

        headers: list[object] = ["Metric"]
        headers += present
        headers += [f"Δ {mode}" for mode in present if mode != reference]
        spec: list[tuple[str, object, str]] = []
        for k in k_values:
            for metric, label in (
                ("recall", f"recall@{k}"),
                ("purity", f"purity@{k}"),
                ("mrr", f"MRR@{k}"),
            ):
                spec.append(
                    (
                        label,
                        lambda mode, metric=metric, k=k: _summary_metric(
                            by_mode[mode], metric, k
                        ),
                        "ratio",
                    )
                )
        spec.append(
            (
                "no-hit rate",
                lambda mode: (by_mode[mode].get("summary") or {}).get(
                    "unanswerable_no_hit_rate"
                ),
                "percent",
            )
        )
        for phase, label in (
            ("search", "search p50 ms"),
            ("search", "search p95 ms"),
            ("e2e", "e2e p50 ms"),
            ("e2e", "e2e p95 ms"),
        ):
            stat = "p50_ms" if label.endswith("p50 ms") else "p95_ms"
            spec.append(
                (
                    label,
                    lambda mode, phase=phase, stat=stat: _summary_timing(
                        by_mode[mode], phase, stat
                    ),
                    "ms2",
                )
            )
        spec.append(
            (
                "rerank cost RUB",
                lambda mode: (by_mode[mode].get("rerank") or {}).get("cost_rub"),
                "cost",
            )
        )

        rows: list[list[object]] = []
        for label, getter, kind in spec:
            values = {mode: getter(mode) for mode in present}
            row: list[object] = [label]
            row += [_fmt_metric(values[mode], kind) for mode in present]
            for mode in present:
                if mode == reference:
                    continue
                row.append(
                    _delta_metric(
                        compare.delta(values[reference], values[mode]), kind
                    )
                )
            rows.append(row)
        blocks.append(layout.table(headers, rows, numeric_from=1))

        qheaders: list[object] = ["id", "Question", "Answerable"]
        qheaders += [f"recall@{max_k} {mode}" for mode in present]
        qheaders += [f"MRR@{max_k} {mode}" for mode in present]
        questions = {mode: _by_question(by_mode[mode]) for mode in present}

        ids: list[str] = []
        for mode in present:
            for qid in questions[mode]:
                if qid not in ids:
                    ids.append(qid)

        qrows: list[list[object]] = []
        for qid in ids:
            viewed = questions[present[0]].get(qid) or {}
            answerable = viewed.get("answerable", True)
            row = [
                qid,
                viewed.get("question", ""),
                "yes" if answerable else "no (negative)",
            ]
            for mode in present:
                by_k = (questions[mode].get(qid, {}).get("by_k") or {}).get(
                    str(max_k)
                ) or {}
                row.append(_number(by_k.get("recall")) if answerable else DASH)
            for mode in present:
                by_k = (questions[mode].get(qid, {}).get("by_k") or {}).get(
                    str(max_k)
                ) or {}
                row.append(_number(by_k.get("mrr")) if answerable else DASH)
            qrows.append(row)

        blocks.append(f"<h4>Per question at k={max_k}</h4>")
        blocks.append(layout.table(qheaders, qrows, numeric_from=3))

    return layout.section("Search modes", layout.Raw("".join(blocks)), anchor="modes")


def _quality(series: list[dict], k_values: tuple[int, ...]) -> str:
    blocks: list[str] = []
    for profile, entries in _group_by_profile(series).items():
        blocks.append(f"<h3>{layout.esc(profile)}</h3>")
        for entry in entries:
            label = entry.get("label") or entry.get("mode", loader.UNKNOWN_MODE)
            mean = _summary_metric(entry, "recall", max(k_values))
            summary = layout.Raw(
                layout.esc(label)
                + (
                    f' <span class="badge">mean recall@{max(k_values)} '
                    f"{_number(mean)}</span>"
                    if mean is not None
                    else ""
                )
            )

            body: list[str] = []
            for k in k_values:
                headers = [
                    "id",
                    "Question",
                    "Answerable",
                    f"hit@{k}",
                    f"recall@{k}",
                    f"precision@{k}",
                    f"MRR@{k}",
                    f"purity@{k}",
                    "hits returned",
                ]
                rows: list[list[object]] = []
                for question in entry.get("questions") or []:
                    by_k = (question.get("by_k") or {}).get(str(k)) or {}
                    if question.get("answerable", True):
                        row: list[object] = [
                            question.get("id", ""),
                            question.get("question", ""),
                            "yes",
                            _number(by_k.get("hit"), digits=0),
                            _number(by_k.get("recall")),
                            _number(by_k.get("precision")),
                            _number(by_k.get("mrr")),
                            _number(by_k.get("purity")),
                            _number(by_k.get("hits_returned")),
                        ]
                    else:
                        row = [
                            question.get("id", ""),
                            question.get("question", ""),
                            "no (negative)",
                            f"no_hit={_number(by_k.get('no_hit'), digits=0)}",
                            DASH,
                            DASH,
                            DASH,
                            DASH,
                            _number(by_k.get("hits_returned")),
                        ]
                    rows.append(row)
                body.append(f"<h4>k={k}</h4>")
                body.append(layout.table(headers, rows, numeric_from=3))

            blocks.append(layout.details(summary, layout.Raw("".join(body))))

    return layout.section("Retrieval quality", layout.Raw("".join(blocks)), anchor="quality")


_TIMING_PHASES = (
    ("search", "Search (local: embed + Qdrant)"),
    ("rerank", "Rerank (paid provider)"),
    ("e2e", "End-to-end (HTTP)"),
)


def _timing(series: list[dict]) -> str:
    labels = [_series_label(entry) for entry in series]
    blocks: list[str] = []
    for phase, label in _TIMING_PHASES:
        p50 = [((entry.get("timings") or {}).get(phase) or {}).get("p50_ms") for entry in series]
        p95 = [((entry.get("timings") or {}).get(phase) or {}).get("p95_ms") for entry in series]
        chart = svg.horizontal_grouped_bar_chart(
            labels,
            [
                svg.Series("p50", p50, svg.PALETTE["p50"]),
                svg.Series("p95", p95, svg.PALETTE["p95"]),
            ],
            value_format="0.0f",
            value_label="milliseconds",
            show_values=True,
            title=f"{label} by profile and mode",
        )
        blocks.append(f"<h3>{layout.esc(label)}</h3>")
        blocks.append(
            layout.figure(
                layout.Raw(chart),
                "p50/p95 across questions (one cold sample per question and mode), "
                "not repeated trials.",
            )
        )

    aggregate: list[list[object]] = []
    for phase, label in _TIMING_PHASES:
        for entry in series:
            timing = (entry.get("timings") or {}).get(phase) or {}
            aggregate.append(
                [
                    label,
                    entry.get("profile", ""),
                    entry.get("mode", loader.UNKNOWN_MODE),
                    _number(timing.get("mean_ms"), digits=2),
                    _number(timing.get("p50_ms"), digits=2),
                    _number(timing.get("p95_ms"), digits=2),
                    _number(timing.get("sum_ms"), digits=2),
                ]
            )
    blocks.append("<h3>Aggregate</h3>")
    blocks.append(
        layout.table(
            ["Phase", "Profile", "Mode", "mean ms", "p50 ms", "p95 ms", "sum ms"],
            aggregate,
            numeric_from=3,
        )
    )

    rerank_rows: list[list[object]] = []
    any_calls = False
    for entry in series:
        rerank = entry.get("rerank") or {}
        calls = rerank.get("calls") or 0
        any_calls = any_calls or calls > 0
        rerank_rows.append(
            [
                entry.get("profile", ""),
                entry.get("mode", loader.UNKNOWN_MODE),
                _number(calls),
                _number(rerank.get("failures")),
                _number(rerank.get("documents")),
                _number(rerank.get("tokens")),
                _number(rerank.get("cost"), digits=6),
                _number(rerank.get("cost_rub"), digits=6),
            ]
        )
    if any_calls:
        blocks.append("<h3>Rerank cost</h3>")
        blocks.append(
            layout.table(
                [
                    "Profile",
                    "Mode",
                    "calls",
                    "failures",
                    "documents",
                    "tokens",
                    "cost",
                    "cost RUB",
                ],
                rerank_rows,
                numeric_from=2,
            )
        )

    return layout.section("Timing", layout.Raw("".join(blocks)), anchor="timing")


def _degradation(results: dict, k_values: tuple[int, ...]) -> str:
    degradation = loader.degradation_modes(results)
    if not degradation:
        return layout.section(
            "Cross-corpus degradation",
            layout.Raw("<p>Only one profile was measured; there is no single→multi delta.</p>"),
            anchor="degradation",
        )

    entry = next(iter(degradation.values()))
    compared = entry.get("questions_compared") or []
    shared = f"{len(compared)} ({', '.join(compared)})" if compared else _number(0)
    mode_metrics = entry.get("modes") or {}

    modes = [mode for mode in loader.SEARCH_MODES if mode in mode_metrics]
    modes += [mode for mode in mode_metrics if mode not in modes]

    facts: list[tuple[str, object]] = [
        ("From → to", f"{entry.get('from', '')} → {entry.get('to', '')}"),
        ("Shared questions", shared),
    ]

    rows: list[list[object]] = []
    for mode in modes:
        metrics = mode_metrics.get(mode) or {}
        for metric, label in (
            ("recall", "Δ recall"),
            ("purity", "Δ purity"),
            ("mrr", "Δ MRR"),
        ):
            rows.append(
                [mode, label]
                + [_number(metrics.get(f"{metric}@{k}")) for k in k_values]
            )

    p95_rows = [
        [mode, _number((mode_metrics.get(mode) or {}).get("p95_ms"), digits=2)]
        for mode in modes
    ]

    delta_chart = svg.grouped_bar_chart(
        [f"k={k}" for k in k_values],
        [
            svg.Series(
                f"Δ recall@k — {mode}",
                [(mode_metrics.get(mode) or {}).get(f"recall@{k}") for k in k_values],
                _mode_color(mode),
            )
            for mode in modes
        ],
        value_format="0.2f",
        show_values=len(modes) == 1,
        y_label="Δ mean recall@k",
        title="Cross-corpus recall degradation by mode",
    )
    body = layout.kv_table(facts)
    body += "<h4>Quality delta</h4>"
    body += layout.table(
        ["Mode", "Metric"] + [f"@{k}" for k in k_values],
        rows,
        numeric_from=2,
    )
    body += "<h4>Latency delta</h4>"
    body += layout.table(["Mode", "Δ e2e p95 ms"], p95_rows, numeric_from=1)
    body += layout.figure(
        layout.Raw(delta_chart),
        "Multi-project minus single-project mean recall@k over the shared "
        "questions, per search mode.",
    )
    return layout.section("Cross-corpus degradation", layout.Raw(body), anchor="degradation")


def _appendix(results: dict, profiles: list[str]) -> str:
    config = results.get("config") or {}
    blocks: list[str] = []
    for name in profiles:
        snapshot = config.get(name) or {}
        blocks.append(f"<h3>{layout.esc(name)}</h3>")
        for filename in ("rig-rag.toml", "sources.json"):
            if filename in snapshot:
                blocks.append(f"<h4>{layout.esc(filename)}</h4>")
                blocks.append(layout.code_block(json.dumps(snapshot[filename], indent=2, sort_keys=True)))
    if not blocks:
        return ""
    return layout.section(
        "Appendix — measured configuration",
        layout.Raw("".join(blocks)),
        anchor="appendix",
    )


def _methodology(has_retrieval: bool, has_ingest: bool) -> str:
    definitions: list[tuple[str, str]] = []
    if has_retrieval:
        definitions += [
            ("hit@k", "1 when any gold item is covered by the top-k hits."),
            ("recall@k", "distinct gold items covered by the top-k hits / total gold items."),
            ("precision@k", "top-k hits covering at least one gold item / hits returned."),
            ("MRR@k", "reciprocal rank of the first hit covering any gold item."),
            ("source purity@k", "share of returned hits from the question's gold source(s)."),
            (
                "no-hit rate",
                "for unanswerable questions, the share of (question, k) pairs with no hit "
                "above the threshold, averaged over every unanswerable question and reported k.",
            ),
            (
                "search mode",
                "hybrid fuses dense and BM25 sparse candidates with reciprocal rank "
                "fusion (the deployed default); dense is cosine over embeddings; sparse "
                "is BM25 only and never embeds the query locally.",
            ),
            (
                "reported k",
                "one uncached query per question per mode at max(k); smaller-k metrics "
                "are exact prefixes of that mode's ranked list because the reranker's "
                "candidate pool covers max(k). There is no warmup or repetition.",
            ),
            (
                "hit list",
                "results.json stores scalar metrics at every reported k and one ranked "
                "hit-location list at max(k); the smaller-k lists are exact prefixes, so "
                "they are not duplicated.",
            ),
            (
                "scores",
                "hybrid scores are RRF, dense are cosine, sparse are BM25 — each on its "
                "own scale. When a reranker is configured the returned score is the "
                "reranker's relevance for that hit, so it is comparable across modes for "
                "the same query; without one, compare scores only within a mode.",
            ),
            (
                "timing",
                "search = embedding + Qdrant; rerank = the paid provider call; e2e = the "
                "client's HTTP round trip. Percentiles are across questions (one cold "
                "sample per question and mode), not repeated trials.",
            ),
        ]
    if has_ingest:
        definitions += [
            (
                "status",
                "created = new collection; rebuilt = existing collection rebuilt with "
                "--force; reused = collection already existed, so cost was not measured.",
            ),
            (
                "min chunk tokens",
                "chunks below this token count are skipped before embedding "
                "(0 = every chunk kept).",
            ),
            (
                "chunk-token distribution",
                "the embedded chunks' token sizes by bucket, counted with the report "
                "tokenizer (an approximation for the embedding model's vocabulary).",
            ),
        ]
    if not definitions:
        return ""
    items = "".join(f"<dt>{layout.esc(name)}</dt><dd>{layout.esc(text)}</dd>" for name, text in definitions)
    return layout.section("Methodology", layout.Raw(f'<dl class="method">{items}</dl>'), anchor="methodology")


def _footer(
    results: dict | None,
    results_path: object,
    ingest: list[dict],
    ingest_paths: list[object],
) -> str:
    inputs = []
    if results_path:
        inputs.append(f"retrieval {results_path}")
    inputs += [str(path) for path in ingest_paths]
    source = f"Render input: {'; '.join(inputs)}. " if inputs else ""

    versions = []
    if results is not None:
        versions.append(f"results schema_version {results.get('schema_version')}")
    if ingest:
        ingest_versions = ", ".join(str(doc.get("schema_version")) for doc in ingest)
        versions.append(f"ingest schema_version {ingest_versions}")
    from_text = f" from {' and '.join(versions)}" if versions else ""
    return (
        "<footer>"
        f"{layout.esc(source)}Generated by <code>eval/report/framework</code>{layout.esc(from_text)}."
        "</footer>"
    )


def _fmt_metric(value: object, kind: str) -> str:
    if value is None:
        return DASH
    if kind == "bytes":
        return _bytes(value)
    if kind == "ms":
        return _number(value, digits=0)
    if kind == "ms2":
        return _number(value, digits=2)
    if kind == "ratio":
        return _number(value, digits=3)
    if kind == "percent":
        return _percent(value)
    if kind == "cost":
        return _number(value, digits=6)
    return _number(value, digits=0)


def _signed(value: object, kind: str) -> str:
    if value is None:
        return DASH
    if value == 0:
        return "0"
    return ("+" if value > 0 else "-") + _fmt_metric(abs(value), kind)


def _signed_percent(value: object) -> str:
    if value is None:
        return DASH
    if value == 0:
        return "0%"
    return ("+" if value > 0 else "-") + _percent(abs(value), digits=1)


def _delta_metric(value: object, kind: str) -> object:
    """A delta cell; a real change is bolded so regressions/improvements stand out."""

    text = _signed(value, kind)
    if value not in (None, 0):
        return layout.Raw(f"<strong>{layout.esc(text)}</strong>")
    return text


def _delta_cell(value: object) -> object:
    return _delta_metric(value, "ratio")


def _candidate_label(results: dict | None) -> str:
    if results is None:
        return "candidate"
    experiment = results.get("experiment") or "experiment"
    run = results.get("run") or ""
    return f"{experiment} ({run})" if run else str(experiment)


def _comparison(
    candidate_results: dict | None,
    candidate_ingest: list[dict],
    baseline: compare.Baseline,
    k_values: tuple[int, ...],
    commit: str | None = None,
) -> str:
    profiles = compare.combined_profiles(candidate_results, tuple(candidate_ingest), baseline)
    if not profiles:
        return ""

    cand_ingest = {ingest_contract.profile_label(doc): doc for doc in candidate_ingest}
    base_ingest = {ingest_contract.profile_label(doc): doc for doc in baseline.ingest}
    cand_cost = {profile: compare.ingest_metrics(cand_ingest.get(profile)) for profile in profiles}
    base_cost = {profile: compare.ingest_metrics(base_ingest.get(profile)) for profile in profiles}

    blocks: list[str] = []
    # An ingest-only candidate declares its commit out of band; fall back to it so
    # this table agrees with Provenance instead of showing an em dash.
    candidate_commit = (candidate_results or {}).get("commit") or (commit or "")
    baseline_commit = (baseline.results or {}).get("commit") or ""
    blocks.append(
        layout.kv_table(
            [
                ("Candidate", _candidate_label(candidate_results)),
                ("Baseline", baseline.label),
                ("Candidate commit", _short_sha(candidate_commit) if candidate_commit else DASH),
                ("Baseline commit", _short_sha(baseline_commit) if baseline_commit else DASH),
            ]
        )
    )

    # ---- ingest cost ----
    if any(cand_cost[profile] or base_cost[profile] for profile in profiles):
        headers: list[object] = ["Metric"]
        for profile in profiles:
            headers += [f"{profile} base", f"{profile} cand", "Δ", "Δ%"]
        rows: list[list[object]] = []
        for key, label, kind in compare.INGEST_ROWS:
            row: list[object] = [label]
            for profile in profiles:
                base_value = base_cost[profile].get(key)
                candidate_value = cand_cost[profile].get(key)
                row += [
                    _fmt_metric(base_value, kind),
                    _fmt_metric(candidate_value, kind),
                    _signed(compare.delta(base_value, candidate_value), kind),
                    _signed_percent(compare.percent(base_value, candidate_value)),
                ]
            rows.append(row)
        body = "<h3>Cost</h3>" + layout.table(headers, rows, numeric_from=1)
        for key, label in (("embeddings", "Embeddings"), ("embed", "Embed ms")):
            chart = svg.grouped_bar_chart(
                profiles,
                [
                    svg.Series("baseline", [base_cost[p].get(key) for p in profiles], svg.PALETTE["muted"]),
                    svg.Series("candidate", [cand_cost[p].get(key) for p in profiles], svg.PALETTE["single"]),
                ],
                value_format="0.0f",
                y_label=label,
                title=f"{label}: baseline vs candidate",
            )
            body += layout.figure(layout.Raw(chart), f"{label} by profile — baseline vs candidate.")
        reused = [
            profile
            for profile in profiles
            if base_ingest.get(profile) is not None and not ingest_contract.cost_measured(base_ingest[profile])
        ]
        if reused:
            body += (
                f'<p class="warn">Baseline cost not measured for '
                f'{layout.esc(", ".join(reused))} — the collection was reused. Rebuild with '
                "<code>--force</code> to diff cost.</p>"
            )
        blocks.append(body)

    # ---- retrieval ----
    if candidate_results is not None and baseline.results is not None:
        pairs, only_candidate, only_baseline = compare.pair_series(
            candidate_results, baseline.results
        )
        candidate_modes = loader.recorded_modes(candidate_results)
        baseline_modes = loader.recorded_modes(baseline.results)

        warnings_out: list[str] = []
        if baseline_modes is None and candidate_modes is not None:
            warnings_out.append(
                "Baseline records no search mode (schema v2); retrieval cannot be "
                "paired until it is re-recorded. Ingest cost is still compared."
            )
        elif candidate_modes is None and baseline_modes is not None:
            warnings_out.append(
                "Candidate records no search mode (schema v2); retrieval cannot be "
                "paired until it is re-recorded."
            )
        if only_candidate:
            warnings_out.append(
                "Candidate-only series (no baseline match): "
                + ", ".join(
                    _series_key(profile, mode) for profile, mode in only_candidate
                )
            )
        if only_baseline:
            warnings_out.append(
                "Baseline-only series (no candidate match): "
                + ", ".join(
                    _series_key(profile, mode) for profile, mode in only_baseline
                )
            )
        if not pairs and not warnings_out:
            warnings_out.append(
                "No (profile, mode) series match between candidate and baseline."
            )
        for warning_text in warnings_out:
            blocks.append(f'<p class="warn">{layout.esc(warning_text)}</p>')

        if pairs:
            mode_order: list[str] = []
            for _, mode, _, _ in pairs:
                if mode not in mode_order:
                    mode_order.append(mode)

            for mode in mode_order:
                mode_pairs = [pair for pair in pairs if pair[1] == mode]
                mode_profiles = [pair[0] for pair in mode_pairs]
                cand_ret = {
                    profile: compare.retrieval_metrics(entry, k_values)
                    for profile, _, entry, _ in mode_pairs
                }
                base_ret = {
                    profile: compare.retrieval_metrics(entry, k_values)
                    for profile, _, _, entry in mode_pairs
                }

                spec: list[tuple[str, str, str]] = []
                for k in k_values:
                    spec.append((f"recall@{k}", f"recall@{k}", "ratio"))
                for k in k_values:
                    spec.append((f"purity@{k}", f"purity@{k}", "ratio"))
                for k in k_values:
                    spec.append((f"mrr@{k}", f"MRR@{k}", "ratio"))
                spec += [
                    ("search_p50_ms", "search p50 ms", "ms2"),
                    ("search_p95_ms", "search p95 ms", "ms2"),
                    ("rerank_p50_ms", "rerank p50 ms", "ms2"),
                    ("rerank_p95_ms", "rerank p95 ms", "ms2"),
                    ("p50_ms", "e2e p50 ms", "ms2"),
                    ("p95_ms", "e2e p95 ms", "ms2"),
                    ("no_hit_rate", "no-hit rate", "ratio"),
                ]
                headers: list[object] = ["Metric"]
                for profile in mode_profiles:
                    headers += [f"{profile} base", f"{profile} cand", "Δ"]
                rows: list[list[object]] = []
                for key, label, kind in spec:
                    row: list[object] = [label]
                    for profile in mode_profiles:
                        base_value = base_ret[profile].get(key)
                        candidate_value = cand_ret[profile].get(key)
                        row += [
                            _fmt_metric(base_value, kind),
                            _fmt_metric(candidate_value, kind),
                            _signed(compare.delta(base_value, candidate_value), kind),
                        ]
                    rows.append(row)

                chart_series = []
                for profile in mode_profiles:
                    color = _profile_color(profile)
                    chart_series.append(
                        svg.Series(
                            f"{profile} baseline",
                            [base_ret[profile].get(f"recall@{k}") for k in k_values],
                            color,
                            dashed=True,
                        )
                    )
                    chart_series.append(
                        svg.Series(
                            f"{profile} candidate",
                            [cand_ret[profile].get(f"recall@{k}") for k in k_values],
                            color,
                        )
                    )
                chart = svg.line_chart(
                    [str(k) for k in k_values],
                    chart_series,
                    y_min=0.0,
                    y_max=1.0,
                    x_label="k",
                    y_label="mean recall@k",
                    title=f"Mean recall@k — {mode}: baseline vs candidate",
                )
                body = f"<h3>Mode: {layout.esc(mode)}</h3>"
                body += layout.table(headers, rows, numeric_from=1)
                body += layout.figure(
                    layout.Raw(chart),
                    "Mean recall@k by profile — dashed = baseline, solid = candidate.",
                )
                blocks.append(body)

            # ---- per question, across every reported k ----
            qheaders: list[object] = ["Mode", "Profile", "id", "k"]
            qheaders += [
                "recall base",
                "recall cand",
                "Δ recall",
                "purity base",
                "purity cand",
                "Δ purity",
            ]
            qrows: list[list[object]] = []
            for profile, mode, candidate_entry, baseline_entry in pairs:
                base_q = compare.question_metrics(baseline_entry, k_values)
                cand_q = compare.question_metrics(candidate_entry, k_values)
                for qid in list(cand_q) + [q for q in base_q if q not in cand_q]:
                    base_q_entry = base_q.get(qid, {})
                    cand_q_entry = cand_q.get(qid, {})
                    for k in k_values:
                        recall_key = f"recall@{k}"
                        purity_key = f"purity@{k}"
                        recall_delta = compare.delta(
                            base_q_entry.get(recall_key), cand_q_entry.get(recall_key)
                        )
                        purity_delta = compare.delta(
                            base_q_entry.get(purity_key), cand_q_entry.get(purity_key)
                        )
                        qrows.append(
                            [
                                mode,
                                profile,
                                qid,
                                str(k),
                                _fmt_metric(base_q_entry.get(recall_key), "ratio"),
                                _fmt_metric(cand_q_entry.get(recall_key), "ratio"),
                                _delta_cell(recall_delta),
                                _fmt_metric(base_q_entry.get(purity_key), "ratio"),
                                _fmt_metric(cand_q_entry.get(purity_key), "ratio"),
                                _delta_cell(purity_delta),
                            ]
                        )
            blocks.append("<h3>Per question</h3>")
            blocks.append(layout.table(qheaders, qrows, numeric_from=4))

    return layout.section("Comparison — candidate vs baseline", layout.Raw("".join(blocks)), anchor="comparison")


def render_report(
    results: dict | None = None,
    *,
    baseline: compare.Baseline | None = None,
    ingest: list[dict] | tuple[dict, ...] = (),
    title: str | None = None,
    subtitle: str | None = None,
    notes: list[str] | tuple[str, ...] = (),
    results_path: object = None,
    ingest_paths: list[object] | tuple[object, ...] = (),
    commit: str | None = None,
    experiment: str | None = None,
) -> str:
    """A complete self-contained HTML document for one run's report parts.

    `results` is optional: pass it for a retrieval section, `ingest` for one or
    more ingestion sections, or both for a combined document. `commit` supplies
    provenance when no `results.json` is present (an ingest-only report declares
    the measured commit itself).
    """

    ingest_docs = list(ingest)

    if results is None and not ingest_docs:
        raise loader.ResultsError(
            "nothing to render: pass a results document and/or ingest reports"
        )

    if results is not None:
        loader.validate_results(results)
        k_values = loader.swept_k_values(results)
        profiles = loader.ordered_profiles(results)
        series = loader.series(results)
        modes = loader.recorded_modes(results) or ()
        name = experiment or str(results.get("experiment") or "experiment")
    else:
        k_values = ()
        profiles = []
        series = []
        modes = ()
        name = experiment or ""

    for document in ingest_docs:
        ingest_contract.validate_ingest(document)

    if baseline is not None:
        if baseline.results is not None:
            loader.validate_results(baseline.results, source=f"{baseline.label} results.json")
        for document in baseline.ingest:
            ingest_contract.validate_ingest(document, source=f"{baseline.label} ingest report")

    if title is None:
        if results is None:
            title = f"Ingestion report — {name}" if name else "Ingestion report"
        elif ingest_docs:
            title = f"Evaluation — {name}" if name else "Evaluation report"
        else:
            title = f"Retrieval evaluation — {name}" if name else "Retrieval evaluation"

    ingest_path_list = list(ingest_paths)
    parts = [
        _header(title, results, subtitle),
        _provenance(results, k_values, ingest_docs, commit),
        _notes(notes) if notes else "",
        _comparison(results, ingest_docs, baseline, k_values, commit=commit)
        if baseline is not None
        else "",
        _ingestion(ingest_docs, ingest_path_list) if ingest_docs else "",
        _summary(series, k_values) if results is not None else "",
        (
            _modes_section(series, k_values, modes)
            if results is not None and len(modes) > 1
            else ""
        ),
        _quality(series, k_values) if results is not None else "",
        _timing(series) if results is not None else "",
        _degradation(results, k_values) if results is not None else "",
        _appendix(results, profiles) if results is not None else "",
        _methodology(results is not None, bool(ingest_docs)),
        _footer(results, results_path, ingest_docs, ingest_path_list),
    ]
    return layout.page(title, "\n".join(part for part in parts if part))

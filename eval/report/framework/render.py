"""Render committed report parts into one self-contained HTML document.

The retrieval part (`results.json`) and the ingestion part
(`ingest-<profile>.json`, one per profile) are independent, separately
versioned artifacts. This module composes whichever parts a run contains, so a
retrieval-only, ingest-only, or combined document all render. Every part is
validated by its own loader before anything is drawn.

The document is assembled from the smaller `layout` helpers and `svg` figures:
provenance, retrieval quality (mean recall@k plus a per-question breakdown),
latency, cross-corpus degradation, ingestion cost, and the exact configuration
the run was measured with. Nothing here reaches the network.
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
    latency_k: int,
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
        pairs.append(("Swept k", ", ".join(str(k) for k in k_values)))
        pairs.append(("Latency measured at", f"k={latency_k}"))
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


def _summary(results: dict, profiles: list[str], k_values: tuple[int, ...], latency_k: int) -> str:
    summary = results.get("summary") or {}
    headers: list[object] = ["Profile", "Questions", "Answerable", "Unanswerable"]
    headers += [f"recall@{k}" for k in k_values]
    headers += [f"purity@{latency_k}", f"MRR@{latency_k}", "no-hit rate", "p50 ms", "p95 ms"]

    rows: list[list[object]] = []
    for name in profiles:
        entry = summary.get(name) or {}
        mean_recall = entry.get("mean_recall") or {}
        latency = entry.get("latency") or {}
        row: list[object] = [
            name,
            _number(entry.get("questions_applied")),
            _number(entry.get("answerable")),
            _number(entry.get("unanswerable")),
        ]
        row += [_number(mean_recall.get(f"recall@{k}")) for k in k_values]
        row += [
            _number(entry.get(f"mean_purity@{latency_k}")),
            _number(entry.get(f"mean_mrr@{latency_k}")),
            _percent(entry.get("unanswerable_no_hit_rate")),
            _number(latency.get("p50_ms"), digits=2),
            _number(latency.get("p95_ms"), digits=2),
        ]
        rows.append(row)

    return layout.section(
        "Summary",
        layout.Raw(layout.table(headers, rows, numeric_from=1)),
        anchor="summary",
    )


def _quality(
    results: dict, profiles: list[str], k_values: tuple[int, ...], latency_k: int
) -> str:
    summary = results.get("summary") or {}
    series = []
    for name in profiles:
        mean_recall = (summary.get(name) or {}).get("mean_recall") or {}
        values = [mean_recall.get(f"recall@{k}") for k in k_values]
        series.append(svg.Series(name=name, values=values, color=_profile_color(name)))

    chart = svg.line_chart(
        [str(k) for k in k_values],
        series,
        y_min=0.0,
        y_max=1.0,
        x_label="k",
        y_label="mean recall@k",
        title="Mean recall@k by profile",
    )
    figure = layout.figure(
        layout.Raw(chart),
        "Mean recall@k over each profile's applicable answerable questions "
        "(question sets may differ between profiles; see degradation for the "
        "shared-question comparison).",
    )

    blocks = [figure]
    for name in profiles:
        profile = (results.get("profiles") or {}).get(name) or {}
        headers = [
            "id",
            "Question",
            "Answerable",
            f"hit@{latency_k}",
            f"recall@{latency_k}",
            f"precision@{latency_k}",
            f"MRR@{latency_k}",
            f"purity@{latency_k}",
            "hits returned",
        ]
        rows: list[list[object]] = []
        for question in profile.get("questions", []):
            by_k = (question.get("by_k") or {}).get(str(latency_k)) or {}
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
        blocks.append(f"<h3>{layout.esc(name)}</h3>")
        blocks.append(layout.table(headers, rows, numeric_from=3))

    return layout.section("Retrieval quality", layout.Raw("".join(blocks)), anchor="quality")


def _latency(results: dict, profiles: list[str]) -> str:
    p50 = []
    p95 = []
    rows: list[list[object]] = []
    for name in profiles:
        latency = ((results.get("profiles") or {}).get(name) or {}).get("latency") or {}
        p50.append(latency.get("p50_ms"))
        p95.append(latency.get("p95_ms"))
        rows.append(
            [
                name,
                _number(latency.get("warmup")),
                _number(latency.get("reps")),
                _number(latency.get("latency_k")),
                _number(latency.get("p50_ms"), digits=2),
                _number(latency.get("p95_ms"), digits=2),
                _number(len(latency.get("samples_ms") or [])),
            ]
        )

    chart = svg.grouped_bar_chart(
        profiles,
        [
            svg.Series("p50", p50, svg.PALETTE["p50"]),
            svg.Series("p95", p95, svg.PALETTE["p95"]),
        ],
        value_format="0.1f",
        y_label="milliseconds",
        title="Query latency by profile",
    )
    body = layout.figure(
        layout.Raw(chart),
        "Sequential /api/query latency after warmup, measured at the profile's latency k.",
    )
    body += layout.table(
        ["Profile", "warmup", "reps", "latency k", "p50 ms", "p95 ms", "samples"],
        rows,
        numeric_from=1,
    )
    return layout.section("Latency", layout.Raw(body), anchor="latency")


def _degradation(results: dict, k_values: tuple[int, ...], latency_k: int) -> str:
    degradation = results.get("degradation") or {}
    if not degradation:
        return layout.section(
            "Cross-corpus degradation",
            layout.Raw("<p>Only one profile was measured; there is no single→multi delta.</p>"),
            anchor="degradation",
        )

    entry = next(iter(degradation.values()))
    compared = entry.get("questions_compared") or []
    shared = f"{len(compared)} ({', '.join(compared)})" if compared else _number(0)
    pairs: list[tuple[str, object]] = [
        ("From → to", f"{entry.get('from', '')} → {entry.get('to', '')}"),
        ("Shared questions", shared),
    ]
    for k in k_values:
        pairs.append((f"Δ recall@{k}", _number(entry.get(f"recall@{k}"))))
    pairs.append((f"Δ purity@{latency_k}", _number(entry.get(f"purity@{latency_k}"))))
    pairs.append(("Δ p95 ms", _number(entry.get("p95_ms"), digits=2)))

    delta_chart = svg.grouped_bar_chart(
        [f"k={k}" for k in k_values],
        [svg.Series("Δ recall@k", [entry.get(f"recall@{k}") for k in k_values], svg.PALETTE["multi"])],
        value_format="0.2f",
        show_values=True,
        y_label="Δ mean recall@k",
        title="Cross-corpus recall degradation",
    )
    body = layout.kv_table(pairs)
    body += layout.figure(
        layout.Raw(delta_chart),
        "Multi-project minus single-project mean recall@k over the shared questions.",
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
                "above the threshold, averaged over every unanswerable question and swept k.",
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


def _delta_cell(value: object) -> object:
    """A delta cell; a real change is bolded so regressions/improvements stand out."""

    text = _signed(value, "ratio")
    if value not in (None, 0):
        return layout.Raw(f"<strong>{layout.esc(text)}</strong>")
    return text


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
    latency_k: int,
) -> str:
    profiles = compare.combined_profiles(candidate_results, tuple(candidate_ingest), baseline)
    if not profiles:
        return ""

    cand_ingest = {ingest_contract.profile_label(doc): doc for doc in candidate_ingest}
    base_ingest = {ingest_contract.profile_label(doc): doc for doc in baseline.ingest}
    cand_cost = {profile: compare.ingest_metrics(cand_ingest.get(profile)) for profile in profiles}
    base_cost = {profile: compare.ingest_metrics(base_ingest.get(profile)) for profile in profiles}

    blocks: list[str] = []
    candidate_commit = (candidate_results or {}).get("commit") or ""
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
        cand_ret = {
            profile: compare.retrieval_metrics(candidate_results, profile, k_values, latency_k)
            for profile in profiles
        }
        base_ret = {
            profile: compare.retrieval_metrics(baseline.results, profile, k_values, latency_k)
            for profile in profiles
        }
        spec: list[tuple[str, str, str]] = [
            (f"recall@{k}", f"recall@{k}", "ratio") for k in k_values
        ]
        spec += [
            (f"purity@{latency_k}", f"purity@{latency_k}", "ratio"),
            (f"mrr@{latency_k}", f"MRR@{latency_k}", "ratio"),
            ("p50_ms", "p50 ms", "ms2"),
            ("p95_ms", "p95 ms", "ms2"),
            ("no_hit_rate", "no-hit rate", "ratio"),
        ]
        headers = ["Metric"]
        for profile in profiles:
            headers += [f"{profile} base", f"{profile} cand", "Δ"]
        rows = []
        for key, label, kind in spec:
            row: list[object] = [label]
            for profile in profiles:
                base_value = base_ret[profile].get(key)
                candidate_value = cand_ret[profile].get(key)
                row += [
                    _fmt_metric(base_value, kind),
                    _fmt_metric(candidate_value, kind),
                    _signed(compare.delta(base_value, candidate_value), kind),
                ]
            rows.append(row)

        series = []
        for profile in profiles:
            color = _profile_color(profile)
            series.append(
                svg.Series(
                    f"{profile} baseline",
                    [base_ret[profile].get(f"recall@{k}") for k in k_values],
                    color,
                    dashed=True,
                )
            )
            series.append(
                svg.Series(
                    f"{profile} candidate",
                    [cand_ret[profile].get(f"recall@{k}") for k in k_values],
                    color,
                )
            )
        chart = svg.line_chart(
            [str(k) for k in k_values],
            series,
            y_min=0.0,
            y_max=1.0,
            x_label="k",
            y_label="mean recall@k",
            title="Mean recall@k: baseline vs candidate",
        )
        body = "<h3>Retrieval</h3>" + layout.table(headers, rows, numeric_from=1)
        body += layout.figure(
            layout.Raw(chart), "Mean recall@k by profile — dashed = baseline, solid = candidate."
        )
        blocks.append(body)

        # ---- per question ----
        qheaders: list[object] = ["Profile", "id"]
        qheaders += [
            f"recall@{latency_k} base",
            f"recall@{latency_k} cand",
            "Δ recall",
            f"purity@{latency_k} base",
            f"purity@{latency_k} cand",
            "Δ purity",
        ]
        qrows: list[list[object]] = []
        for profile in profiles:
            base_q = compare.question_metrics(baseline.results, profile, latency_k)
            cand_q = compare.question_metrics(candidate_results, profile, latency_k)
            for qid in list(cand_q) + [q for q in base_q if q not in cand_q]:
                base_q_entry = base_q.get(qid, {})
                cand_q_entry = cand_q.get(qid, {})
                recall_delta = compare.delta(base_q_entry.get("recall"), cand_q_entry.get("recall"))
                purity_delta = compare.delta(base_q_entry.get("purity"), cand_q_entry.get("purity"))
                qrows.append(
                    [
                        profile,
                        qid,
                        _fmt_metric(base_q_entry.get("recall"), "ratio"),
                        _fmt_metric(cand_q_entry.get("recall"), "ratio"),
                        _delta_cell(recall_delta),
                        _fmt_metric(base_q_entry.get("purity"), "ratio"),
                        _fmt_metric(cand_q_entry.get("purity"), "ratio"),
                        _delta_cell(purity_delta),
                    ]
                )
        body = "<h3>Per question</h3>" + layout.table(qheaders, qrows, numeric_from=2)
        blocks.append(body)

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
        latency_k = loader.latency_k(results)
        profiles = loader.ordered_profiles(results)
        name = experiment or str(results.get("experiment") or "experiment")
    else:
        k_values = ()
        latency_k = loader.DEFAULT_LATENCY_K
        profiles = []
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
        _provenance(results, k_values, latency_k, ingest_docs, commit),
        _notes(notes) if notes else "",
        _comparison(results, ingest_docs, baseline, k_values, latency_k) if baseline is not None else "",
        _ingestion(ingest_docs, ingest_path_list) if ingest_docs else "",
        _summary(results, profiles, k_values, latency_k) if results is not None else "",
        _quality(results, profiles, k_values, latency_k) if results is not None else "",
        _latency(results, profiles) if results is not None else "",
        _degradation(results, k_values, latency_k) if results is not None else "",
        _appendix(results, profiles) if results is not None else "",
        _methodology(results is not None, bool(ingest_docs)),
        _footer(results, results_path, ingest_docs, ingest_path_list),
    ]
    return layout.page(title, "\n".join(part for part in parts if part))

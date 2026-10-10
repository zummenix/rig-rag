"""Render a committed `results.json` into a self-contained HTML report.

The document is assembled from the smaller `layout` helpers and `svg` figures:
provenance, a per-profile summary, retrieval quality (mean recall@k plus a
per-question breakdown), latency, cross-corpus degradation, and the exact
configuration the run was measured with. Nothing here reaches the network.
"""

from __future__ import annotations

import json

from eval.report.framework import layout, loader, svg

DASH = "\u2013"  # en dash for an undefined value


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
    text = f"{number:.{digits}f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _percent(value: object, *, digits: int = 1) -> str:
    if value is None:
        return DASH
    try:
        return f"{float(value) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return str(value)


def _profile_color(name: str) -> str:
    if name == "single-project":
        return svg.PALETTE["single"]
    if name == "multi-project":
        return svg.PALETTE["multi"]
    return svg.PALETTE["p50"]


def _header(title: str, results: dict, subtitle: str | None) -> str:
    if subtitle is None:
        run = results.get("run", "")
        started = results.get("started_at", "")
        subtitle = " · ".join(part for part in (f"run {run}" if run else "", started) if part)
    return (
        f"<header><h1>{layout.esc(title)}</h1>"
        f'<p class="subtitle">{layout.esc(subtitle)}</p></header>'
    )


def _provenance(results: dict, k_values: tuple[int, ...], latency_k: int) -> str:
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
    known = ("os", "arch", "python", "binary", "binary_sha256", "qdrant_url")
    env_pairs = [(key, environment[key]) for key in known if key in environment]

    body = layout.kv_table(pairs)
    if env_pairs:
        body += "<h4>Environment</h4>" + layout.kv_table(env_pairs)
    return layout.section("Provenance", layout.Raw(body), anchor="provenance")


def _notes(notes: list[str] | tuple[str, ...]) -> str:
    items = "".join(f"<li>{layout.esc(note)}</li>" for note in notes)
    return layout.section("Notes", layout.Raw(f'<ul class="notes">{items}</ul>'))


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
                    _number(by_k.get("hits_returned")),
                ]
            rows.append(row)
        blocks.append(f"<h4>{layout.esc(name)}</h4>")
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
    pairs: list[tuple[str, object]] = [
        ("From → to", f"{entry.get('from', '')} → {entry.get('to', '')}"),
        ("Shared questions", f"{len(compared)} ({', '.join(compared)})" if compared else _number(0)),
    ]
    for k in k_values:
        pairs.append((f"Δ recall@{k}", entry.get(f"recall@{k}")))
    pairs.append((f"Δ purity@{latency_k}", entry.get(f"purity@{latency_k}")))
    pairs.append(("Δ p95 ms", entry.get("p95_ms")))

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


def _methodology() -> str:
    definitions = [
        ("hit@k", "1 when any gold item is covered by the top-k hits."),
        ("recall@k", "distinct gold items covered by the top-k hits / total gold items."),
        ("precision@k", "top-k hits covering at least one gold item / hits returned."),
        ("MRR@k", "reciprocal rank of the first hit covering any gold item."),
        ("source purity@k", "share of returned hits from the question's gold source(s)."),
        ("no-hit rate", "for unanswerable questions, the share of k with no hit above threshold."),
    ]
    items = "".join(f"<dt>{layout.esc(name)}</dt><dd>{layout.esc(text)}</dd>" for name, text in definitions)
    return layout.section("Methodology", layout.Raw(f'<dl class="method">{items}</dl>'), anchor="methodology")


def _footer(results: dict, results_path: object) -> str:
    version = results.get("schema_version")
    source = f"Render input: {results_path}." if results_path else ""
    return (
        "<footer>"
        f"{layout.esc(source)} Generated by <code>eval/report/framework</code> from a "
        f"schema_version {layout.esc(version)} results.json."
        "</footer>"
    )


def render_report(
    results: dict,
    *,
    title: str | None = None,
    subtitle: str | None = None,
    notes: list[str] | tuple[str, ...] = (),
    results_path: object = None,
) -> str:
    """A complete self-contained HTML document for one `results.json`."""

    loader.validate_results(results)
    k_values = loader.swept_k_values(results)
    latency_k = loader.latency_k(results)
    profiles = loader.ordered_profiles(results)
    experiment = str(results.get("experiment") or "experiment")
    title = title or f"Retrieval evaluation — {experiment}"

    parts = [
        _header(title, results, subtitle),
        _provenance(results, k_values, latency_k),
        _notes(notes) if notes else "",
        _summary(results, profiles, k_values, latency_k),
        _quality(results, profiles, k_values, latency_k),
        _latency(results, profiles),
        _degradation(results, k_values, latency_k),
        _appendix(results, profiles),
        _methodology(),
        _footer(results, results_path),
    ]
    return layout.page(title, "\n".join(part for part in parts if part))

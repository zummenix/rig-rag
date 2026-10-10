"""Inline-SVG chart primitives for the self-contained report.

Every function returns a complete `<svg>` element (a `str`) ready to drop into a
`<figure>`. The output is pure geometry and text — no external stylesheet, font,
image, or script — so a report renders identically from disk and from an
archive. Callers wrap the result (see `render`); the primitives only draw.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

# A small palette shared across the report's figures.
PALETTE = {
    "single": "#2563eb",  # blue
    "multi": "#dc2626",  # red
    "p50": "#0f766e",  # teal
    "p95": "#b45309",  # amber
    "positive": "#15803d",  # green
    "negative": "#b91c1c",  # red
    "grid": "#e5e7eb",
    "axis": "#9ca3af",
    "ink": "#111827",
    "muted": "#6b7280",
    "surface": "#ffffff",
}

_FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"

# The legend lives in the top margin, above the plot area, so it can never sit
# on top of a bar or a data point. The y-axis title shares the legend's text
# baseline so the two read as one line.
_LEGEND_TOP = 8
_LEGEND_SWATCH = 10
_LEGEND_TEXT_Y = _LEGEND_TOP + _LEGEND_SWATCH - 1
_TOP_MARGIN = 34


@dataclass(frozen=True)
class Series:
    """One named series of values aligned with the chart's x labels.

    A `None` value is a gap: the point is skipped and the line breaks. This is
    how a metric that is absent for some k is drawn without inventing a zero.
    A `dashed` series draws a dashed line (and dashes its legend swatch), so a
    baseline and a candidate of the same color can share one chart.
    """

    name: str
    values: list[float | None]
    color: str = PALETTE["single"]
    dashed: bool = False


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def _fmt(value: float) -> str:
    """A compact number: no trailing zeros, at most three decimals."""

    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


@dataclass(frozen=True)
class _Frame:
    width: int
    height: int
    left: int
    right: int
    top: int
    bottom: int

    @property
    def x0(self) -> float:
        return float(self.left)

    @property
    def x1(self) -> float:
        return float(self.width - self.right)

    @property
    def y0(self) -> float:
        return float(self.top)

    @property
    def y1(self) -> float:
        return float(self.height - self.bottom)

    @property
    def plot_w(self) -> float:
        return self.x1 - self.x0

    @property
    def plot_h(self) -> float:
        return self.y1 - self.y0


def _scale(value: float, lo: float, hi: float, out_lo: float, out_hi: float) -> float:
    if hi <= lo:
        hi = lo + 1.0
    return out_lo + (value - lo) / (hi - lo) * (out_hi - out_lo)


def _ticks(lo: float, hi: float, count: int) -> list[float]:
    count = max(2, count)
    if hi <= lo:
        hi = lo + 1.0
    step = (hi - lo) / (count - 1)
    return [lo + step * index for index in range(count)]


def _svg_open(width: int, height: int, title: str) -> list[str]:
    label = f' role="img" aria-label="{_esc(title)}"' if title else ' role="img"'
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}"{label}>']
    if title:
        parts.append(f"<title>{_esc(title)}</title>")
    return parts


def _legend(entries: list[tuple[str, str, bool]], frame: _Frame) -> str:
    """A top-right legend drawn from `(label, color, dashed)` entries.

    A dashed entry is drawn as a dashed line swatch (matching a dashed series);
    a solid one as a filled square.
    """

    if not entries:
        return ""
    y = _LEGEND_TOP
    x = frame.x1
    swatch = _LEGEND_SWATCH
    mid = y + swatch / 2
    parts: list[str] = []
    for label, color, dashed in reversed(entries):
        entry_width = swatch + 4 + len(label) * 6.2
        x -= entry_width + 12
        if dashed:
            parts.append(
                f'<line x1="{x:.1f}" y1="{mid:.1f}" x2="{x + swatch:.1f}" y2="{mid:.1f}" '
                f'stroke="{_esc(color)}" stroke-width="2" stroke-dasharray="4 3"/>'
            )
        else:
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{swatch}" height="{swatch}" rx="2" '
                f'fill="{_esc(color)}"/>'
            )
        parts.append(
            f'<text x="{x + swatch + 4:.1f}" y="{_LEGEND_TEXT_Y:.1f}" font-family="{_FONT}" '
            f'font-size="11" fill="{PALETTE["ink"]}">{_esc(label)}</text>'
        )
    return "".join(parts)


def _gridlines(lo: float, hi: float, ticks: list[float], frame: _Frame, value_format: str) -> str:
    parts: list[str] = []
    for value in ticks:
        y = _scale(value, lo, hi, frame.y1, frame.y0)
        parts.append(
            f'<line x1="{frame.x0:.1f}" y1="{y:.1f}" x2="{frame.x1:.1f}" y2="{y:.1f}" '
            f'stroke="{PALETTE["grid"]}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{frame.x0 - 8:.1f}" y="{y + 3.5:.1f}" text-anchor="end" '
            f'font-family="{_FONT}" font-size="11" fill="{PALETTE["muted"]}">'
            f"{_esc(format(value, value_format))}</text>"
        )
    return "".join(parts)


def _segments(values: list[float | None]) -> list[list[tuple[int, float]]]:
    """Runs of consecutive non-`None` values as `(index, value)` pairs."""

    segments: list[list[tuple[int, float]]] = []
    current: list[tuple[int, float]] = []
    for index, value in enumerate(values):
        if value is None:
            if current:
                segments.append(current)
                current = []
            continue
        current.append((index, value))
    if current:
        segments.append(current)
    return segments


def line_chart(
    x_labels: list[str],
    series: list[Series],
    *,
    y_min: float = 0.0,
    y_max: float = 1.0,
    y_ticks: int = 5,
    value_format: str = "0.2f",
    x_label: str = "",
    y_label: str = "",
    title: str = "",
    width: int = 680,
    height: int = 300,
) -> str:
    """A line chart of one or more `Series` over shared `x_labels`."""

    count = len(x_labels)
    frame = _Frame(width, height, left=58, right=16, top=_TOP_MARGIN, bottom=52)
    parts = _svg_open(width, height, title)
    parts.append(_gridlines(y_min, y_max, _ticks(y_min, y_max, y_ticks), frame, value_format))

    positions = [
        frame.x0 + (frame.plot_w / 2 if count == 1 else frame.plot_w * index / (count - 1))
        for index in range(count)
    ]
    for position, label in zip(positions, x_labels):
        parts.append(
            f'<text x="{position:.1f}" y="{frame.y1 + 18:.1f}" text-anchor="middle" '
            f'font-family="{_FONT}" font-size="11" fill="{PALETTE["muted"]}">{_esc(label)}</text>'
        )

    for entry in series:
        if len(entry.values) != count:
            raise ValueError(
                f"series {entry.name!r} has {len(entry.values)} values for {count} x labels"
            )
        for segment in _segments(entry.values):
            points = [
                (positions[index], _scale(value, y_min, y_max, frame.y1, frame.y0))
                for index, value in segment
            ]
            if len(points) > 1:
                parts.append(_polyline(points, entry.color, entry.dashed))
            for x, y in points:
                parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" fill="{_esc(entry.color)}"/>')

    if y_label:
        parts.append(
            f'<text x="{frame.x0 - 42:.1f}" y="{_LEGEND_TEXT_Y:.1f}" font-family="{_FONT}" '
            f'font-size="11" fill="{PALETTE["muted"]}">{_esc(y_label)}</text>'
        )
    if x_label:
        parts.append(
            f'<text x="{(frame.x0 + frame.x1) / 2:.1f}" y="{height - 10:.1f}" text-anchor="middle" '
            f'font-family="{_FONT}" font-size="11" fill="{PALETTE["muted"]}">{_esc(x_label)}</text>'
        )

    parts.append(_legend([(entry.name, entry.color, entry.dashed) for entry in series], frame))
    parts.append("</svg>")
    return "".join(parts)


def _polyline(points: list[tuple[float, float]], color: str, dashed: bool = False) -> str:
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    dash = ' stroke-dasharray="5 4"' if dashed else ""
    return (
        f'<polyline points="{path}" fill="none" stroke="{_esc(color)}" '
        f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"{dash}/>'
    )


def grouped_bar_chart(
    groups: list[str],
    series: list[Series],
    *,
    value_format: str = "0.2f",
    y_label: str = "",
    title: str = "",
    show_values: bool = False,
    width: int = 680,
    height: int = 320,
) -> str:
    """A grouped vertical bar chart; negative values diverge below a zero line."""

    for entry in series:
        if len(entry.values) != len(groups):
            raise ValueError(
                f"series {entry.name!r} has {len(entry.values)} values for "
                f"{len(groups)} groups"
            )

    values = [value for entry in series for value in entry.values if value is not None]
    lo = min([0.0, *values])
    hi = max([0.0, *values])
    span = (hi - lo) or abs(hi) or 1.0
    if lo < 0:
        lo -= span * 0.08
        hi += span * 0.08
    else:
        lo = 0.0
        hi += span * 0.08

    frame = _Frame(width, height, left=58, right=16, top=_TOP_MARGIN, bottom=52)
    parts = _svg_open(width, height, title)
    parts.append(_gridlines(lo, hi, _ticks(lo, hi, 5), frame, value_format))

    zero_y = _scale(0.0, lo, hi, frame.y1, frame.y0)
    if lo < 0:
        parts.append(
            f'<line x1="{frame.x0:.1f}" y1="{zero_y:.1f}" x2="{frame.x1:.1f}" y2="{zero_y:.1f}" '
            f'stroke="{PALETTE["axis"]}" stroke-width="1.5"/>'
        )

    group_count = len(groups)
    series_count = max(1, len(series))
    group_w = frame.plot_w / group_count if group_count else frame.plot_w
    bar_w = group_w * 0.7 / series_count

    for group_index, group in enumerate(groups):
        group_x = frame.x0 + group_w * group_index
        parts.append(
            f'<text x="{group_x + group_w / 2:.1f}" y="{frame.y1 + 18:.1f}" text-anchor="middle" '
            f'font-family="{_FONT}" font-size="11" fill="{PALETTE["muted"]}">{_esc(group)}</text>'
        )
        for series_index, entry in enumerate(series):
            value = entry.values[group_index]
            if value is None:
                continue
            x = group_x + group_w * 0.15 + series_index * bar_w
            y = _scale(value, lo, hi, frame.y1, frame.y0)
            top = min(y, zero_y)
            bar_h = max(1.0, abs(y - zero_y))
            parts.append(
                f'<rect x="{x:.1f}" y="{top:.1f}" width="{bar_w * 0.92:.1f}" height="{bar_h:.1f}" '
                f'rx="1.5" fill="{_esc(entry.color)}"/>'
            )
            if show_values:
                label_y = top - 5 if value >= 0 else top + bar_h + 12
                parts.append(
                    f'<text x="{x + bar_w * 0.46:.1f}" y="{label_y:.1f}" text-anchor="middle" '
                    f'font-family="{_FONT}" font-size="10" fill="{PALETTE["ink"]}">'
                    f"{_esc(format(value, value_format))}</text>"
                )

    if y_label:
        parts.append(
            f'<text x="{frame.x0 - 42:.1f}" y="{_LEGEND_TEXT_Y:.1f}" font-family="{_FONT}" '
            f'font-size="11" fill="{PALETTE["muted"]}">{_esc(y_label)}</text>'
        )

    parts.append(_legend([(entry.name, entry.color, entry.dashed) for entry in series], frame))
    parts.append("</svg>")
    return "".join(parts)


# One color per phase row in a waterfall, cycling if there are more rows.
_PHASE_COLORS = (
    "#2563eb",  # blue
    "#0f766e",  # teal
    "#b45309",  # amber
    "#7c3aed",  # violet
    "#dc2626",  # red
    "#0891b2",  # cyan
)


def waterfall(
    phases: list[tuple[str, float]],
    *,
    total: float | None = None,
    title: str = "",
    width: int = 720,
    row_height: int = 24,
) -> str:
    """A horizontal timeline of sequential phases, like a network waterfall.

    Each ``(label, duration)`` is drawn as a bar that starts where the previous
    one ended: the offset is the cumulative start and the width is the duration.
    All values share one unit (the caller's). When ``total`` is given it sets the
    axis span, so any time not attributed to a listed phase shows as trailing
    space rather than being silently redistributed.
    """

    rows = [(label, max(0.0, float(value))) for label, value in phases]
    if not rows:
        raise ValueError("waterfall needs at least one phase")
    summed = sum(value for _, value in rows)
    axis_max = max([summed, float(total or 0.0), 1.0])

    label_w = 96
    value_w = 72
    right = 12
    top = 10
    bottom = 28
    height = top + row_height * len(rows) + bottom
    x0 = label_w
    x1 = width - right - value_w
    plot_w = max(1.0, x1 - x0)
    base_y = top + row_height * len(rows)

    def number(value: float) -> str:
        return f"{value:.1f}" if axis_max < 50 else f"{value:,.0f}"

    parts = _svg_open(width, height, title)
    for tick in _ticks(0.0, axis_max, 5):
        x = x0 + plot_w * (tick / axis_max)
        parts.append(
            f'<line x1="{x:.1f}" y1="{top - 2:.1f}" x2="{x:.1f}" y2="{base_y + 4:.1f}" '
            f'stroke="{PALETTE["grid"]}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{base_y + 18:.1f}" text-anchor="middle" '
            f'font-family="{_FONT}" font-size="10" fill="{PALETTE["muted"]}">'
            f"{_esc(number(tick))}</text>"
        )

    cumulative = 0.0
    for index, (label, value) in enumerate(rows):
        row_y = top + index * row_height
        bar_y = row_y + row_height * 0.2
        bar_h = row_height * 0.6
        start = x0 + plot_w * (cumulative / axis_max)
        end = x0 + plot_w * ((cumulative + value) / axis_max)
        mid_y = row_y + row_height * 0.72
        parts.append(
            f'<text x="{x0 - 8:.1f}" y="{mid_y:.1f}" text-anchor="end" '
            f'font-family="{_FONT}" font-size="11" fill="{PALETTE["ink"]}">{_esc(label)}</text>'
        )
        parts.append(
            f'<rect x="{start:.1f}" y="{bar_y:.1f}" width="{max(1.0, end - start):.1f}" '
            f'height="{bar_h:.1f}" rx="1.5" fill="{_esc(_PHASE_COLORS[index % len(_PHASE_COLORS)])}"/>'
        )
        parts.append(
            f'<text x="{width - right:.1f}" y="{mid_y:.1f}" text-anchor="end" '
            f'font-family="{_FONT}" font-size="10" fill="{PALETTE["muted"]}">{_esc(number(value))}</text>'
        )
        cumulative += value

    parts.append("</svg>")
    return "".join(parts)

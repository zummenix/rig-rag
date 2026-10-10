"""HTML scaffolding and presentational helpers for the report.

The document is deliberately self-contained: one inline `<style>`, inline SVG,
system fonts, no scripts and no network references, so a report opens from disk
(or from an archive) and always renders the same. All caller-supplied text is
escaped here; pre-rendered markup is passed as `Raw`.
"""

from __future__ import annotations

import html

STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0 auto; padding: 2rem clamp(1rem, 5vw, 3rem) 4rem; max-width: 1100px;
  font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  color: #111827; background: #ffffff;
}
h1 { font-size: 1.7rem; line-height: 1.2; margin: 0 0 .3rem; }
h2 { font-size: 1.25rem; margin: 2.4rem 0 .6rem; padding-bottom: .3rem; border-bottom: 1px solid #e5e7eb; }
h3 { font-size: 1.05rem; margin: 1.6rem 0 .4rem; }
h4 { font-size: .95rem; margin: 1.2rem 0 .3rem; color: #374151; }
p { margin: .5rem 0; }
p.subtitle { color: #6b7280; margin: 0 0 1.2rem; }
table { border-collapse: collapse; width: 100%; margin: .4rem 0 1.2rem; font-size: .9rem; }
th, td { text-align: left; padding: .35rem .6rem; border-bottom: 1px solid #e5e7eb; vertical-align: top; }
thead th { border-bottom: 2px solid #9ca3af; white-space: nowrap; }
tbody tr:hover { background: #f9fafb; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
figure { margin: 1rem 0 1.4rem; }
figcaption { color: #6b7280; font-size: .85rem; margin-top: .35rem; }
svg { max-width: 100%; height: auto; display: block; }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .84rem; }
pre { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 6px; padding: .75rem; overflow-x: auto; }
.scroll { overflow-x: auto; }
.badge { display: inline-block; padding: .05rem .45rem; border-radius: 999px; font-size: .78rem;
  border: 1px solid #e5e7eb; color: #374151; background: #f9fafb; }
.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: .5rem 1.5rem; }
ul.notes { margin: .4rem 0 1rem; padding-left: 1.2rem; }
ul.notes li { margin: .25rem 0; }
.statusline { margin: .2rem 0 .6rem; }
p.warn { background: #fffbeb; border: 1px solid #fde68a; border-radius: 6px;
  padding: .5rem .7rem; margin: .2rem 0 1rem; color: #92400e; font-size: .9rem; }
footer { margin-top: 3rem; color: #6b7280; font-size: .82rem; border-top: 1px solid #e5e7eb; padding-top: 1rem; }
dl.method { margin: .4rem 0 1rem; }
dl.method dt { font-weight: 600; margin-top: .5rem; }
dl.method dd { margin: .1rem 0 0 0; color: #374151; }
"""


class Raw(str):
    """Marks already-safe markup so the layout helpers do not re-escape it."""


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _markup(value: object) -> str:
    return str(value) if isinstance(value, Raw) else esc(value)


def page(title: str, body: str, *, lang: str = "en") -> str:
    """A complete, self-contained HTML5 document."""

    return (
        "<!doctype html>\n"
        f'<html lang="{esc(lang)}">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{esc(title)}</title>\n"
        f"<style>\n{STYLE}</style>\n"
        "</head>\n<body>\n"
        f"{body}\n"
        "</body>\n</html>\n"
    )


def section(heading: str, body: object, *, level: int = 2, anchor: str | None = None) -> str:
    identifier = f' id="{esc(anchor)}"' if anchor else ""
    return f"<section{identifier}>\n<h{level}>{esc(heading)}</h{level}>\n{_markup(body)}\n</section>\n"


def table(
    headers: list[object],
    rows: list[list[object]],
    *,
    numeric_from: int | None = None,
    caption: str | None = None,
) -> str:
    """A table; columns at/after `numeric_from` are right-aligned."""

    def cell_class(index: int) -> str:
        return ' class="num"' if numeric_from is not None and index >= numeric_from else ""

    head = "".join(f"<th{cell_class(i)}>{_markup(header)}</th>" for i, header in enumerate(headers))
    body = "".join(
        "<tr>"
        + "".join(f"<td{cell_class(i)}>{_markup(cell)}</td>" for i, cell in enumerate(row))
        + "</tr>"
        for row in rows
    )
    caption_html = f"<caption>{esc(caption)}</caption>" if caption else ""
    return (
        f'<div class="scroll"><table>{caption_html}<thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def kv_table(pairs: list[tuple[object, object]]) -> str:
    """A two-column key/value table, used for provenance and environment blocks."""

    rows = "".join(
        f'<tr><th scope="row">{esc(key)}</th><td>{_markup(value)}</td></tr>' for key, value in pairs
    )
    return f'<div class="scroll"><table><tbody>{rows}</tbody></table></div>'


def figure(svg: object, caption: str) -> str:
    return f"<figure>{_markup(svg)}<figcaption>{esc(caption)}</figcaption></figure>"


def code_block(text: str) -> str:
    return f"<pre><code>{esc(text)}</code></pre>"


def badge(text: str) -> str:
    return f'<span class="badge">{esc(text)}</span>'

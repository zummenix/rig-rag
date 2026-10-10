"""Gold-evidence matching for retrieved hits.

A hit covers a gold item when:

* the hit's source (the path component under the profile data root) equals the
  gold `source`;
* the hit path equals the gold `path` or ends with `/` + it (a whole-path
  suffix, so `testimonials.md` matches `data/<profile>/jj/testimonials.md` but
  not `my-testimonials.md`);
* the hit's line range overlaps the gold `lines` (inclusive) when given; and
* `must_contain` occurs verbatim in the hit text when given.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from eval.questions import Gold


def source_of(path: str, data_root: str) -> str | None:
    """The source name encoded in a stored document path.

    Stored paths are the filesystem paths the loader walks, e.g.
    `data/single-project/jj/docs/foo.md` with data root `data/single-project`,
    so the source is the first component under the data root.
    """

    root = PurePosixPath(str(data_root).replace("\\", "/"))
    candidate = PurePosixPath(str(path).replace("\\", "/"))
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return None
    parts = relative.parts
    return parts[0] if parts else None


def path_matches(hit_path: str, gold_path: str) -> bool:
    hit_path = hit_path.replace("\\", "/")
    gold_path = gold_path.replace("\\", "/").lstrip("/")
    return hit_path == gold_path or hit_path.endswith("/" + gold_path)


def lines_overlap(hit_lines: tuple[int, int], gold_lines: tuple[int, int]) -> bool:
    hit_start, hit_end = hit_lines
    gold_start, gold_end = gold_lines
    return hit_start <= gold_end and gold_start <= hit_end


def hit_covers(hit: dict, gold: Gold, data_root: str) -> bool:
    if source_of(hit["path"], data_root) != gold.source:
        return False
    if not path_matches(hit["path"], gold.path):
        return False
    if gold.lines is not None:
        hit_lines = (int(hit["start_line"]), int(hit["end_line"]))
        if not lines_overlap(hit_lines, gold.lines):
            return False
    if gold.must_contain is not None and gold.must_contain not in hit["text"]:
        return False
    return True


def covering_gold_indexes(hit: dict, gold: tuple[Gold, ...], data_root: str) -> list[int]:
    """Indexes into `gold` that `hit` covers (usually zero or one)."""

    return [index for index, item in enumerate(gold) if hit_covers(hit, item, data_root)]

"""The versioned retrieval-eval question set (`eval/questions.toml`).

A question applies to a profile only when every `gold.source` exists in that
profile; `gold: []` marks an unanswerable question, whose applicability is
authored explicitly.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

QUESTIONS_PATH = "eval/questions.toml"

# Profile names the runner knows about, most specific first. The single-project
# corpus is a strict subset of the multi-project one; tests assert this.
SINGLE_PROFILE = "single-project"
MULTI_PROFILE = "multi-project"


@dataclass(frozen=True)
class Gold:
    """One piece of evidence that a correct top-k retrieval should surface."""

    source: str
    path: str
    lines: tuple[int, int] | None = None
    must_contain: str | None = None


@dataclass(frozen=True)
class Question:
    """One evaluation question and its gold evidence."""

    id: str
    question: str
    applies_to: tuple[str, ...]
    gold: tuple[Gold, ...]
    notes: str = ""

    @property
    def answerable(self) -> bool:
        return bool(self.gold)

    def gold_sources(self) -> set[str]:
        return {item.source for item in self.gold}


def _parse_gold(raw: dict) -> Gold:
    lines = raw.get("lines")
    if lines is not None:
        if not isinstance(lines, list) or len(lines) != 2:
            raise ValueError(f"gold.lines must be a 2-element list, got {lines!r}")
        lines = (int(lines[0]), int(lines[1]))
        if lines[0] > lines[1]:
            raise ValueError(f"gold.lines start must not exceed end, got {lines!r}")
    return Gold(
        source=str(raw["source"]),
        path=str(raw["path"]),
        lines=lines,
        must_contain=raw.get("must_contain"),
    )


def parse_questions(raw: str) -> list[Question]:
    document = tomllib.loads(raw)
    entries = document.get("question")
    if not isinstance(entries, list) or not entries:
        raise ValueError("questions file must contain a non-empty [[question]] array")
    questions: list[Question] = []
    for entry in entries:
        questions.append(
            Question(
                id=str(entry["id"]),
                question=str(entry["question"]),
                applies_to=tuple(str(p) for p in entry.get("applies_to", [])),
                gold=tuple(_parse_gold(item) for item in entry.get("gold", [])),
                notes=str(entry.get("notes", "")),
            )
        )
    return questions


def load_questions(path: str | Path) -> list[Question]:
    path = Path(path)
    return parse_questions(path.read_text(encoding="utf-8"))


def validate_questions(questions: list[Question], profile_sources: dict[str, set[str]]) -> None:
    """Checks the authored set against the discovered corpus profiles.

    Enforced invariants:
      * ids are unique and non-empty;
      * every `applies_to` profile is known;
      * a question is only applied to profiles that contain all its gold sources;
      * every answerable question is applied to every profile that contains all
        its gold sources (so the `jj` questions apply to both profiles);
      * every single-project question is also a multi-project question.
    """

    known = set(profile_sources)
    seen: set[str] = set()
    for question in questions:
        if not question.id:
            raise ValueError("question id must be non-empty")
        if question.id in seen:
            raise ValueError(f"duplicate question id {question.id!r}")
        seen.add(question.id)

        if not question.question:
            raise ValueError(f"question {question.id!r} has an empty question string")

        unknown = set(question.applies_to) - known
        if unknown:
            raise ValueError(
                f"question {question.id!r} applies to unknown profile(s) {sorted(unknown)}"
            )
        if not question.applies_to:
            raise ValueError(f"question {question.id!r} applies to no profile")

        if SINGLE_PROFILE in question.applies_to and MULTI_PROFILE not in question.applies_to:
            raise ValueError(
                f"question {question.id!r} applies to {SINGLE_PROFILE!r} but not "
                f"{MULTI_PROFILE!r}; the single-project set must be a subset"
            )

        for profile in question.applies_to:
            missing = question.gold_sources() - profile_sources[profile]
            if missing:
                raise ValueError(
                    f"question {question.id!r} applies to {profile!r} but its gold "
                    f"source(s) {sorted(missing)} are absent there"
                )

        if question.answerable:
            for profile, sources in profile_sources.items():
                if question.gold_sources() <= sources and profile not in question.applies_to:
                    raise ValueError(
                        f"question {question.id!r} has all gold sources in {profile!r} "
                        "but does not apply to it"
                    )


def applicable_questions(questions: list[Question], profile: str) -> list[Question]:
    return [question for question in questions if profile in question.applies_to]

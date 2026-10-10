import unittest

from eval.questions import (
    Gold,
    parse_questions,
    validate_questions,
)

PROFILE_SOURCES = {
    "single-project": {"jj"},
    "multi-project": {"jj", "podman", "qdrant"},
}


def question_toml(**overrides) -> str:
    base = {
        "id": "q1",
        "question": "who?",
        "applies": '["single-project", "multi-project"]',
        "gold": '[{ source = "jj", path = "a.md" }]',
        "notes": "",
    }
    base.update(overrides)
    return (
        "[[question]]\n"
        f'id = "{base["id"]}"\n'
        f'question = "{base["question"]}"\n'
        f'applies_to = {base["applies"]}\n'
        f"gold = {base['gold']}\n"
        f'notes = "{base["notes"]}"\n'
    )


class ParseQuestionsTest(unittest.TestCase):
    def test_parses_gold_fields(self):
        raw = question_toml(
            gold='[{ source = "jj", path = "a.md", lines = [3, 9], must_contain = "hi" }]'
        )
        question = parse_questions(raw)[0]
        self.assertEqual(question.id, "q1")
        self.assertEqual(question.applies_to, ("single-project", "multi-project"))
        self.assertEqual(
            question.gold,
            (Gold(source="jj", path="a.md", lines=(3, 9), must_contain="hi"),),
        )
        self.assertTrue(question.answerable)

    def test_empty_gold_is_unanswerable(self):
        question = parse_questions(question_toml(gold="[]", applies='["multi-project"]'))[0]
        self.assertFalse(question.answerable)
        self.assertEqual(question.gold_sources(), set())

    def test_rejects_bad_lines(self):
        with self.assertRaises(ValueError):
            parse_questions(question_toml(gold='[{ source = "jj", path = "a.md", lines = [9, 3] }]'))
        with self.assertRaises(ValueError):
            parse_questions(question_toml(gold='[{ source = "jj", path = "a.md", lines = [1] }]'))


class ValidateQuestionsTest(unittest.TestCase):
    def test_accepts_the_seed_shape(self):
        questions = parse_questions(
            question_toml()
            + question_toml(id="q2", applies='["multi-project"]', gold='[{ source = "podman", path = "b.md" }]')
        )
        validate_questions(questions, PROFILE_SOURCES)

    def test_rejects_duplicate_ids(self):
        questions = parse_questions(question_toml() + question_toml())
        with self.assertRaisesRegex(ValueError, "duplicate question id"):
            validate_questions(questions, PROFILE_SOURCES)

    def test_rejects_unknown_profile(self):
        questions = parse_questions(question_toml(applies='["nope"]'))
        with self.assertRaisesRegex(ValueError, "unknown profile"):
            validate_questions(questions, PROFILE_SOURCES)

    def test_rejects_gold_source_absent_from_profile(self):
        questions = parse_questions(
            question_toml(
                applies='["single-project", "multi-project"]',
                gold='[{ source = "podman", path = "b.md" }]',
            )
        )
        with self.assertRaisesRegex(ValueError, "absent there"):
            validate_questions(questions, PROFILE_SOURCES)

    def test_rejects_answerable_question_missing_an_applicable_profile(self):
        # Gold is jj-only, so it must also apply to single-project.
        questions = parse_questions(question_toml(applies='["multi-project"]'))
        with self.assertRaisesRegex(ValueError, "does not apply"):
            validate_questions(questions, PROFILE_SOURCES)

    def test_rejects_single_without_multi(self):
        questions = parse_questions(question_toml(applies='["single-project"]'))
        with self.assertRaisesRegex(ValueError, "subset"):
            validate_questions(questions, PROFILE_SOURCES)

    def test_unanswerable_is_not_forced_into_single(self):
        questions = parse_questions(question_toml(gold="[]", applies='["multi-project"]'))
        validate_questions(questions, PROFILE_SOURCES)


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path

from eval.report.framework import (
    IngestError,
    UnsupportedIngestSchemaVersion,
    cost_measured,
    load_ingest,
    validate_ingest,
)

FIXTURES = Path(__file__).parent / "fixtures"


class LoadIngestTest(unittest.TestCase):
    def test_current_fixture_loads(self):
        document = load_ingest(FIXTURES / "ingest" / "v1" / "ingest-single-project.json")
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(document["status"], "created")

    def test_reused_fixture_loads(self):
        document = load_ingest(FIXTURES / "ingest" / "v1_reused" / "ingest-single-project.json")
        self.assertEqual(document["status"], "reused")

    def test_missing_file(self):
        with self.assertRaises(IngestError):
            load_ingest(FIXTURES / "ingest" / "does-not-exist.json")


class ValidateIngestTest(unittest.TestCase):
    def test_unsupported_newer_version_names_the_version(self):
        with self.assertRaises(UnsupportedIngestSchemaVersion) as caught:
            validate_ingest({"schema_version": 2, "totals": {}})
        self.assertIn("2", str(caught.exception))
        self.assertIn("newer than", str(caught.exception))

    def test_unsupported_older_version(self):
        with self.assertRaises(UnsupportedIngestSchemaVersion) as caught:
            validate_ingest({"schema_version": 0, "totals": {}})
        self.assertIn("older than", str(caught.exception))

    def test_missing_schema_version(self):
        with self.assertRaises(IngestError):
            validate_ingest({"totals": {}})

    def test_bool_is_not_a_valid_version(self):
        with self.assertRaises(IngestError):
            validate_ingest({"schema_version": True, "totals": {}})

    def test_non_object(self):
        with self.assertRaises(IngestError):
            validate_ingest([1, 2, 3])

    def test_missing_totals(self):
        with self.assertRaises(IngestError):
            validate_ingest({"schema_version": 1})

    def test_totals_only_document_is_rejected(self):
        # This shape used to pass and then crash the renderer at
        # `svg.waterfall([])`; it must be an IngestError before rendering now.
        with self.assertRaises(IngestError):
            validate_ingest({"schema_version": 1, "totals": {}})

    def test_empty_timing_is_rejected(self):
        document = load_ingest(FIXTURES / "ingest" / "v1" / "ingest-single-project.json")
        document["timing_ms"] = {}
        with self.assertRaises(IngestError):
            validate_ingest(document)

    def test_non_numeric_timing_phase_is_rejected(self):
        document = load_ingest(FIXTURES / "ingest" / "v1" / "ingest-single-project.json")
        document["timing_ms"]["embed"] = "fast"
        with self.assertRaises(IngestError):
            validate_ingest(document)


class CostMeasuredTest(unittest.TestCase):
    def test_created_is_measured(self):
        self.assertTrue(cost_measured({"status": "created"}))

    def test_rebuilt_is_measured(self):
        self.assertTrue(cost_measured({"status": "rebuilt"}))

    def test_reused_is_not_measured(self):
        self.assertFalse(cost_measured({"status": "reused"}))

    def test_missing_status_is_treated_as_measured(self):
        # Only `reused` short-circuits ingest before counting; anything else
        # (including an older report without the field) carries real numbers.
        self.assertTrue(cost_measured({}))


if __name__ == "__main__":
    unittest.main()

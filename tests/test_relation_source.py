import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.relation_source import RelationSourceValidationError, normalize_relation_source


SOURCE = {
    "profileId": "local",
    "database": "demo",
    "namespace": "public",
    "relation": "orders",
    "kind": "table",
    "fingerprint": "a" * 64,
}


class RelationSourceTests(unittest.TestCase):
    def test_normalizes_exact_identity_and_optional_columns(self):
        source = {
            **SOURCE,
            "columns": [{"name": "id", "type": "x" * 512, "nullable": False, "ordinal": 1}],
        }
        self.assertEqual(normalize_relation_source(source, expected_profile_id="local"), source)

    def test_rejects_type_drift_and_profile_mismatch(self):
        for column_type in (" x", "x ", "x" * 513):
            with self.subTest(column_type=len(column_type)), self.assertRaises(RelationSourceValidationError):
                normalize_relation_source({
                    **SOURCE,
                    "columns": [{"name": "id", "type": column_type, "nullable": False, "ordinal": 1}],
                })
        with self.assertRaises(RelationSourceValidationError):
            normalize_relation_source(SOURCE, expected_profile_id="other")

    def test_enforces_postgresql_identifier_bytes_and_ordered_unique_columns(self):
        self.assertEqual(normalize_relation_source({**SOURCE, "relation": "é" * 31})["relation"], "é" * 31)
        invalid_sources = (
            {**SOURCE, "relation": "é" * 32},
            {**SOURCE, "columns": [
                {"name": "a", "type": "text", "nullable": True, "ordinal": 2},
                {"name": "b", "type": "text", "nullable": True, "ordinal": 1},
            ]},
            {**SOURCE, "columns": [
                {"name": "a", "type": "text", "nullable": True, "ordinal": 1},
                {"name": "a", "type": "text", "nullable": True, "ordinal": 2},
            ]},
        )
        for source in invalid_sources:
            with self.subTest(source=source), self.assertRaises(RelationSourceValidationError):
                normalize_relation_source(source)


if __name__ == "__main__":
    unittest.main()

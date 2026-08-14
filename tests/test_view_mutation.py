import copy
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.postgres_service import ConflictError, PostgresService, PostgresServiceError, ValidationError
from schemii.schema_store import SchemaStore, SchemaStoreError
from tests.test_postgres_service import Connection, PROFILE


FINGERPRINT = "a" * 64


def descriptor(kind="view", fingerprint=FINGERPRINT, dependents=None):
    return {
        "profileId": "local", "database": "demo", "namespace": "public", "relation": "summary",
        "kind": kind, "fingerprint": fingerprint, "columns": [],
        "definition": {"status": "available", "format": "query", "sql": "SELECT 1"},
        "dependents": {"status": "available", "items": dependents or [], "truncated": False},
    }


class ViewMutationServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.connections = []

        def connect(**kwargs):
            connection = Connection(responses={
                "SELECT c.oid, pg_catalog.pg_get_userbyid(c.relowner)": [{
                    "oid": 1, "owner": "developer", "explicit_acl": False, "relation_comment": None,
                    "reloptions": None, "tablespace": None, "access_method": "heap", "populated": True,
                    "triggers": False, "rules": False, "security_labels": False, "storage": False,
                }],
            })
            self.connections.append(connection)
            return connection

        self.service = PostgresService(self.temporary_directory.name, connect_factory=connect)
        self.service.save_profile("local", PROFILE)

    def tearDown(self):
        self.temporary_directory.cleanup()

    @staticmethod
    def binding():
        return {"schemaId": "schema_one", "expectedSchemaRevision": 1, "layoutToken": "0" * 64}

    def test_preview_requires_exact_expectation_definition_kind_and_identity(self):
        self.service._inspect_relation_connection = lambda *args: descriptor()
        valid = {
            "profile_id": "local", "database": "demo", "namespace": "public", "relation": "summary",
            "operation": "upsert",
            "expectation": {"kind": "view", "fingerprint": FINGERPRINT},
            "desired": {"kind": "view", "definition": 'CREATE VIEW "public"."summary" AS SELECT 2'},
            "allow_destructive": False, "schema_binding": self.binding(),
        }
        plan = self.service.preview_view_mutation(**valid)
        self.assertEqual(plan["steps"][0]["sql"], 'CREATE OR REPLACE VIEW "public"."summary" AS SELECT 2;')
        self.assertFalse(plan["destructive"])
        self.assertEqual(plan["warnings"][0]["code"], "view_output_compatibility_apply_validated")
        self.assertNotIn("schemaBinding", plan)

        invalid = [
            {**valid, "expectation": {"absent": False}},
            {**valid, "expectation": {"kind": "view", "fingerprint": FINGERPRINT, "extra": True}},
            {**valid, "desired": {"kind": "materialized_view", "definition": 'CREATE VIEW "public"."summary" AS SELECT 2'}},
            {**valid, "desired": {"kind": "view", "definition": 'CREATE VIEW "other"."summary" AS SELECT 2'}},
            {**valid, "desired": {"kind": "view", "definition": 'CREATE VIEW "public"."summary" AS SELECT 2; SELECT 3'}},
        ]
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValidationError):
                self.service.preview_view_mutation(**item)

    def test_ai_create_view_preview_is_durable_and_rejects_existing_relation(self):
        def missing(*args):
            from schemii.postgres_service import NotFoundError
            raise NotFoundError("missing")

        self.service._inspect_relation_connection = missing
        preview = self.service.preview_ai_create_view(
            "operation_view_preview", "local", "demo", "public", "summary",
            'CREATE VIEW "public"."summary" AS SELECT 1', {"schemaId": "schema_one", "revision": 1, "layoutToken": "0" * 64},
        )
        self.assertEqual(preview["kind"], "create_view")
        self.assertEqual(preview["steps"][0]["action"], "create")
        restarted = PostgresService(self.temporary_directory.name, connect_factory=self.service._connect_factory)
        self.assertEqual(restarted._read_ai_plan(preview["applyPlanId"])["kind"], "create_view")

        self.service._inspect_relation_connection = lambda *args: descriptor()
        with self.assertRaises(ConflictError):
            self.service.preview_ai_create_view(
                "operation_existing_view", "local", "demo", "public", "summary",
                'CREATE VIEW "public"."summary" AS SELECT 1', {"schemaId": "schema_one", "revision": 1, "layoutToken": "0" * 64},
            )

    def test_new_and_existing_materialized_preview_and_kind_conversion_boundary(self):
        def missing(*args):
            from schemii.postgres_service import NotFoundError
            raise NotFoundError("missing")

        self.service._inspect_relation_connection = missing
        created = self.service.preview_view_mutation(
            "local", "demo", "public", "summary", "upsert", {"absent": True},
            {"kind": "materialized_view", "definition": 'CREATE MATERIALIZED VIEW "public"."summary" AS SELECT 1'},
            False, self.binding(),
        )
        self.assertFalse(created["destructive"])
        self.assertEqual(created["steps"][0]["action"], "create")

        self.service._inspect_relation_connection = lambda *args: descriptor("materialized_view")
        desired = {"kind": "materialized_view", "definition": 'CREATE MATERIALIZED VIEW "public"."summary" AS SELECT 2'}
        with self.assertRaises(ConflictError) as error:
            self.service.preview_view_mutation(
                "local", "demo", "public", "summary", "upsert", {"kind": "materialized_view", "fingerprint": FINGERPRINT},
                desired, False, self.binding(),
            )
        self.assertEqual(error.exception.code, "destructive_preview_required")
        recreated = self.service.preview_view_mutation(
            "local", "demo", "public", "summary", "upsert", {"kind": "materialized_view", "fingerprint": FINGERPRINT},
            desired, True, self.binding(),
        )
        self.assertTrue(recreated["destructive"])
        self.assertEqual([step["action"] for step in recreated["steps"][:2]], ["drop", "create"])
        self.assertIn("WITH DATA", recreated["steps"][1]["sql"])
        self.assertEqual(recreated["warnings"][0]["code"], "materialized_rows_repopulated")

        self.service._inspect_relation_connection = lambda *args: descriptor()
        with self.assertRaises(PostgresServiceError) as error:
            self.service.preview_view_mutation(
                "local", "demo", "public", "summary", "upsert", {"kind": "view", "fingerprint": FINGERPRINT},
                desired, True, self.binding(),
            )
        self.assertEqual(error.exception.code, "view_kind_conversion_unsupported")

        self.service._inspect_relation_connection = lambda *args: descriptor("materialized_view")
        with self.assertRaises(PostgresServiceError) as error:
            self.service.preview_view_mutation(
                "local", "demo", "public", "summary", "upsert", {"kind": "materialized_view", "fingerprint": FINGERPRINT},
                {"kind": "view", "definition": 'CREATE VIEW "public"."summary" AS SELECT 2'}, True, self.binding(),
            )
        self.assertEqual(error.exception.code, "view_kind_conversion_unsupported")

        self.service._inspect_relation_connection = missing
        ordinary = self.service.preview_view_mutation(
            "local", "demo", "public", "summary", "upsert", {"absent": True},
            {"kind": "view", "definition": 'CREATE OR REPLACE VIEW "public"."summary" AS SELECT 1'},
            False, self.binding(),
        )
        self.assertEqual(ordinary["steps"][0]["sql"], 'CREATE VIEW "public"."summary" AS SELECT 1;')
        self.assertEqual(ordinary["steps"][0]["action"], "create")

    def test_existing_materialized_recreation_blocks_direct_dependents(self):
        self.service._inspect_relation_connection = lambda *args: descriptor(
            "materialized_view", dependents=[{"database": "demo", "namespace": "public", "relation": "consumer", "kind": "view"}],
        )
        with self.assertRaises(PostgresServiceError) as error:
            self.service.preview_view_mutation(
                "local", "demo", "public", "summary", "upsert", {"kind": "materialized_view", "fingerprint": FINGERPRINT},
                {"kind": "materialized_view", "definition": 'CREATE MATERIALIZED VIEW "public"."summary" AS SELECT 2'},
                True, self.binding(),
            )
        self.assertEqual(error.exception.code, "view_recreation_unsupported")

    def test_existing_materialized_recreation_blocks_unsupported_metadata(self):
        self.connections.clear()

        def connect(**kwargs):
            connection = Connection(responses={
                "SELECT c.oid, pg_catalog.pg_get_userbyid(c.relowner)": [{
                    "oid": 1, "owner": "developer", "explicit_acl": False, "relation_comment": None,
                    "reloptions": None, "tablespace": None, "access_method": "heap", "populated": True,
                    "triggers": True, "rules": True, "security_labels": True, "storage": True,
                }],
            })
            self.connections.append(connection)
            return connection

        self.service._connect_factory = connect
        self.service._inspect_relation_connection = lambda *args: descriptor("materialized_view")
        with self.assertRaises(PostgresServiceError) as error:
            self.service.preview_view_mutation(
                "local", "demo", "public", "summary", "upsert", {"kind": "materialized_view", "fingerprint": FINGERPRINT},
                {"kind": "materialized_view", "definition": 'CREATE MATERIALIZED VIEW "public"."summary" AS SELECT 2'},
                True, self.binding(),
            )
        self.assertEqual(error.exception.code, "view_recreation_unsupported")
        self.assertIn("triggers", error.exception.details["concerns"])

    def test_apply_rechecks_expectation_uses_transaction_controls_and_rolls_back(self):
        states = [descriptor(), descriptor(fingerprint="b" * 64)]

        def inspect(connection, *args):
            connection.executed.append(("MOCK INSPECT RELATION", ()))
            return copy.deepcopy(states.pop(0))

        self.service._inspect_relation_connection = inspect
        plan = self.service.preview_view_mutation(
            "local", "demo", "public", "summary", "upsert", {"kind": "view", "fingerprint": FINGERPRINT},
            {"kind": "view", "definition": 'CREATE VIEW "public"."summary" AS SELECT 2'}, False, self.binding(),
        )
        with self.assertRaises(ConflictError) as error:
            self.service.apply_view_mutation("local", plan["id"], False)
        self.assertEqual(error.exception.code, "relation_changed")
        connection = self.connections[-1]
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        sql = [item[0] for item in connection.executed]
        self.assertIn("BEGIN", sql)
        self.assertTrue(any("lock_timeout" in item for item in sql))
        self.assertTrue(any("statement_timeout" in item for item in sql))
        advisory = next(item for item in connection.executed if "pg_advisory_xact_lock" in item[0])
        self.assertEqual(
            advisory,
            ("SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext(%s)::bigint)", ("schemii:public",)),
        )
        lock_index = sql.index('SELECT * FROM "public"."summary" LIMIT 0')
        inspection_index = sql.index("MOCK INSPECT RELATION", lock_index)
        self.assertLess(lock_index, inspection_index)
        self.assertFalse(any("ACCESS EXCLUSIVE" in item for item in sql))

    def test_apply_rejects_legacy_existing_materialized_plan_before_mutation(self):
        plan = {
            "id": "plan_legacy", "kind": "view_mutation", "profileId": "local", "database": "demo",
            "namespace": "public", "relation": "summary",
            "profileFingerprint": self.service._profile_fingerprint(self.service._profile("local")),
            "expectation": {"kind": "materialized_view", "fingerprint": FINGERPRINT},
            "desiredKind": "materialized_view", "desiredDefinition": "CREATE MATERIALIZED VIEW public.summary AS SELECT 2;",
            "schemaBinding": self.binding(), "allowDestructive": True, "destructive": True,
            "steps": [{"sql": "DROP MATERIALIZED VIEW public.summary", "action": "drop", "objectType": "materialized view", "name": "summary"}],
            "createdAt": self.service._clock(), "expiresAt": self.service._clock() + 60,
        }
        with self.service._lock:
            self.service._plans[plan["id"]] = plan
        with self.assertRaises(PostgresServiceError) as error:
            self.service.apply_view_mutation("local", plan["id"], True)
        self.assertEqual(error.exception.code, "not_found")
        self.assertEqual(self.connections, [])

    def test_apply_returns_refreshed_descriptor_and_history(self):
        states = [descriptor(), descriptor(), descriptor(fingerprint="b" * 64)]
        self.service._inspect_relation_connection = lambda *args: copy.deepcopy(states.pop(0))
        plan = self.service.preview_view_mutation(
            "local", "demo", "public", "summary", "upsert", {"kind": "view", "fingerprint": FINGERPRINT},
            {"kind": "view", "definition": 'CREATE VIEW "public"."summary" AS SELECT 2'}, False, self.binding(),
        )
        result = self.service.apply_view_mutation("local", plan["id"], False)
        self.assertTrue(result["applied"])
        self.assertFalse(result["expectedAbsent"])
        self.assertEqual(result["descriptor"]["fingerprint"], "b" * 64)
        self.assertEqual(self.connections[-1].commits, 1)
        self.assertEqual(self.service.list_history()[0]["planId"], plan["id"])

    def test_expected_absence_duplicate_creation_rolls_back(self):
        from schemii.postgres_service import NotFoundError

        self.service._inspect_relation_connection = lambda *args: (_ for _ in ()).throw(NotFoundError("missing"))
        plan = self.service.preview_view_mutation(
            "local", "demo", "public", "summary", "upsert", {"absent": True},
            {"kind": "view", "definition": 'CREATE VIEW "public"."summary" AS SELECT 1'},
            False, self.binding(),
        )
        self.connections.clear()
        connection = Connection(fail_on="CREATE VIEW", failure=RuntimeError("duplicate relation"))
        self.service._connect_factory = lambda **kwargs: connection
        with self.assertRaises(PostgresServiceError) as error:
            self.service.apply_view_mutation("local", plan["id"], False)
        self.assertEqual(error.exception.code, "apply_failed")
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)

    def test_apply_returns_postgres_diagnostic_and_failed_step(self):
        from schemii.postgres_service import NotFoundError

        class Diagnostic:
            message_primary = "relation missing_source does not exist"
            message_detail = "The view query references an unavailable relation."
            message_hint = "Check the relation namespace."
            statement_position = "48"

        class DatabaseError(Exception):
            sqlstate = "42P01"
            diag = Diagnostic()

        self.service._inspect_relation_connection = lambda *args: (_ for _ in ()).throw(NotFoundError("missing"))
        plan = self.service.preview_view_mutation(
            "local", "demo", "public", "summary", "upsert", {"absent": True},
            {"kind": "view", "definition": 'CREATE VIEW "public"."summary" AS SELECT * FROM missing_source'},
            False, self.binding(),
        )
        connection = Connection(fail_on="CREATE VIEW", failure=DatabaseError())
        self.service._connect_factory = lambda **kwargs: connection
        with self.assertRaises(PostgresServiceError) as caught:
            self.service.apply_view_mutation("local", plan["id"], False)
        self.assertEqual(caught.exception.code, "apply_failed")
        self.assertIn("relation missing_source does not exist", caught.exception.message)
        self.assertEqual(caught.exception.details, {
            "stepIndex": 0,
            "postgres": {
                "sqlstate": "42P01",
                "message": "relation missing_source does not exist",
                "detail": "The view query references an unavailable relation.",
                "hint": "Check the relation namespace.",
            },
        })
        self.assertEqual(connection.rollbacks, 1)

    def test_commit_failure_is_uncertain_and_plan_cannot_be_retried(self):
        states = [descriptor(), descriptor(), descriptor(fingerprint="b" * 64)]
        self.service._inspect_relation_connection = lambda *args: copy.deepcopy(states.pop(0))
        plan = self.service.preview_view_mutation(
            "local", "demo", "public", "summary", "upsert", {"kind": "view", "fingerprint": FINGERPRINT},
            {"kind": "view", "definition": 'CREATE VIEW "public"."summary" AS SELECT 2'}, False, self.binding(),
        )
        connection = Connection()
        connection.commit = lambda: (_ for _ in ()).throw(RuntimeError("connection lost during commit"))
        self.service._connect_factory = lambda **kwargs: connection
        with self.assertRaises(PostgresServiceError) as caught:
            self.service.apply_view_mutation("local", plan["id"], False)
        self.assertEqual(caught.exception.code, "execution_outcome_unknown")
        self.assertEqual(self.service._plans[plan["id"]]["state"], "uncertain")
        with self.assertRaises(ConflictError) as repeated:
            self.service.apply_view_mutation("local", plan["id"], False)
        self.assertEqual(repeated.exception.code, "plan_in_use")

    def test_successful_expected_absent_apply_reports_sync_intent_for_both_view_kinds(self):
        from schemii.postgres_service import NotFoundError

        for kind in ("view", "materialized_view"):
            with self.subTest(kind=kind):
                calls = 0

                def inspect(*args):
                    nonlocal calls
                    calls += 1
                    if calls < 3:
                        raise NotFoundError("missing")
                    return descriptor(kind, fingerprint="b" * 64)

                self.service._inspect_relation_connection = inspect
                keyword = "MATERIALIZED VIEW" if kind == "materialized_view" else "VIEW"
                plan = self.service.preview_view_mutation(
                    "local", "demo", "public", "summary", "upsert", {"absent": True},
                    {"kind": kind, "definition": f'CREATE {keyword} "public"."summary" AS SELECT 1'},
                    False, self.binding(),
                )
                result = self.service.apply_view_mutation("local", plan["id"], False)
                self.assertTrue(result["expectedAbsent"])
                self.assertEqual(result["descriptor"]["kind"], kind)

    def test_delete_preview_and_apply_for_both_view_kinds(self):
        from schemii.postgres_service import NotFoundError

        for kind in ("view", "materialized_view"):
            with self.subTest(kind=kind):
                states = [descriptor(kind), descriptor(kind)]
                def inspect(*args):
                    if states:
                        return copy.deepcopy(states.pop(0))
                    raise NotFoundError("deleted")
                self.service._inspect_relation_connection = inspect
                plan = self.service.preview_view_mutation(
                    "local", "demo", "public", "summary", "delete",
                    {"kind": kind, "fingerprint": FINGERPRINT}, None, True, self.binding(),
                )
                self.assertTrue(plan["destructive"])
                self.assertEqual(plan["operation"], "delete")
                self.assertNotIn("CASCADE", plan["steps"][0]["sql"])
                result = self.service.apply_view_mutation("local", plan["id"], True)
                self.assertEqual(result["operation"], "delete")
                self.assertEqual(result["deleted"]["kind"], kind)
                self.assertNotIn("descriptor", result)
                sql = [item[0] for item in self.connections[-1].executed]
                if kind == "materialized_view":
                    self.assertLess(
                        sql.index('REFRESH MATERIALIZED VIEW "public"."summary" WITH NO DATA'),
                        sql.index('DROP MATERIALIZED VIEW "public"."summary";'),
                    )

    def test_materialized_recreation_restores_metadata_and_rolls_back_failed_restore(self):
        self.connections.clear()
        metadata = {
            "oid": 1, "owner": "developer", "explicit_acl": False, "relation_comment": "summary rows",
            "reloptions": ["fillfactor=80"], "tablespace": "pg_default", "access_method": "heap", "populated": False,
            "triggers": False, "rules": False, "security_labels": False, "storage": False,
        }
        responses = {
            "SELECT c.oid, pg_catalog.pg_get_userbyid(c.relowner)": [metadata],
            "SELECT a.attname AS column_name": [{"column_name": "value", "description": "output"}],
            "SELECT ci.relname AS name": [{
                "name": "summary_value_idx", "definition": 'CREATE INDEX summary_value_idx ON public.summary USING btree (value)',
                "comment": "lookup", "indisvalid": True, "indisready": True,
            }],
        }
        connection = Connection(responses=responses, fail_on="COMMENT ON INDEX", failure=RuntimeError("restore failed"))
        self.service._connect_factory = lambda **kwargs: connection
        self.service._inspect_relation_connection = lambda *args: descriptor("materialized_view")
        plan = self.service.preview_view_mutation(
            "local", "demo", "public", "summary", "upsert",
            {"kind": "materialized_view", "fingerprint": FINGERPRINT},
            {"kind": "materialized_view", "definition": 'CREATE MATERIALIZED VIEW "public"."summary" AS SELECT 2 AS value'},
            True, self.binding(),
        )
        create = next(step["sql"] for step in plan["steps"] if step["action"] == "create")
        self.assertIn("USING \"heap\"", create)
        self.assertIn("WITH (fillfactor = '80')", create)
        self.assertIn('TABLESPACE "pg_default"', create)
        self.assertIn("WITH NO DATA", create)
        with self.assertRaises(PostgresServiceError) as error:
            self.service.apply_view_mutation("local", plan["id"], True)
        self.assertEqual(error.exception.code, "apply_failed")
        self.assertEqual(connection.commits, 0)
        self.assertGreaterEqual(connection.rollbacks, 2)
        self.assertEqual(self.service.view_mutation_binding("local", plan["id"])["operation"], "upsert")


class ViewMutationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = SchemaStore(self.temporary_directory.name)
        self.record = {
            "id": "schema_one", "custom": {"untouched": [1, 2]},
            "schema": {
                "projectName": "Demo", "tables": [{"id": "table_one", "columns": [], "x": 10}],
                "relationships": [], "functions": [],
                "views": [
                    {"id": "view_summary", "name": "summary", "namespace": "public", "materialized": False, "definition": "old", "custom": 1},
                    {"id": "view_other", "name": "other", "namespace": "public", "definition": "unchanged"},
                ],
                "layout": {"version": 2, "layers": {"views": {"objects": {"view_summary": {"x": 44, "y": 55}}}}},
                "postgres": {"sourceProfileId": "local", "database": "demo", "namespace": "public"},
            },
        }
        self.saved = self.store.save("schema_one", self.record, expected_layout_token=None, layout_protocol=None)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_sync_replaces_only_matching_view_and_preserves_layout(self):
        before = self.store.get("schema_one")
        result = self.store.sync_view_after_mutation(
            "schema_one", self.saved["revision"], self.saved["layoutToken"], "local", "demo", "public", "summary",
            "view", 'CREATE OR REPLACE VIEW "public"."summary" AS SELECT 2', "SELECT 2", "b" * 64,
            operation="upsert", expected_absent=False, saved_view_id="view_summary",
        )
        after = self.store.get("schema_one")
        self.assertEqual(result["status"], "saved")
        self.assertEqual(after["schema"]["layout"], before["schema"]["layout"])
        self.assertEqual(after["schema"]["views"][1], before["schema"]["views"][1])
        self.assertEqual(after["custom"], before["custom"])
        self.assertEqual(after["schema"]["views"][0]["custom"], 1)
        self.assertEqual(after["schema"]["views"][0]["queryDefinition"], "SELECT 2")

    def test_sync_appends_expected_absent_ordinary_and_materialized_views_without_layout_changes(self):
        for kind, materialized in (("view", False), ("materialized_view", True)):
            with self.subTest(kind=kind):
                current = self.store.get("schema_one")
                before_layout = copy.deepcopy(current["schema"]["layout"])
                relation = f"new_{kind}"
                self.store.sync_view_after_mutation(
                    "schema_one", current["revision"], self.store.list()[0]["layoutToken"],
                    "local", "demo", "public", relation, kind,
                    f'CREATE {"MATERIALIZED " if materialized else ""}VIEW "public"."{relation}" AS SELECT 1',
                    "SELECT 1", "c" * 64, operation="upsert", expected_absent=True, saved_view_id=None,
                )
                after = self.store.get("schema_one")
                created = [item for item in after["schema"]["views"] if item.get("name") == relation]
                self.assertEqual(len(created), 1)
                self.assertEqual(created[0]["materialized"], materialized)
                self.assertTrue(created[0]["id"].startswith("pg_view_"))
                self.assertEqual(after["schema"]["layout"], before_layout)
                self.assertEqual(after["custom"], current["custom"])

    def test_sync_expected_absent_rejects_saved_collision_or_ambiguity(self):
        for duplicate in (False, True):
            with self.subTest(duplicate=duplicate):
                current = self.store.get("schema_one")
                if duplicate:
                    changed = copy.deepcopy(current)
                    changed["schema"]["views"].append({"name": "summary", "namespace": "public"})
                    saved = self.store.save(
                        "schema_one", changed,
                        expected_layout_token=self.store.list()[0]["layoutToken"], layout_protocol="2",
                    )
                    current = self.store.get("schema_one")
                    token = saved["layoutToken"]
                else:
                    token = self.store.list()[0]["layoutToken"]
                before = copy.deepcopy(current)
                with self.assertRaises(SchemaStoreError) as error:
                    self.store.sync_view_after_mutation(
                        "schema_one", current["revision"], token, "local", "demo", "public", "summary",
                        "view", "CREATE VIEW public.summary AS SELECT 1", "SELECT 1", "d" * 64,
                        operation="upsert", expected_absent=True, saved_view_id=None,
                    )
                self.assertEqual(error.exception.payload["error"]["code"], "schema_view_changed")
                self.assertEqual(self.store.get("schema_one"), before)

    def test_sync_replacement_rejects_missing_or_stale_saved_view(self):
        current = self.store.get("schema_one")
        for relation in ("missing", "summary"):
            with self.subTest(relation=relation), self.assertRaises(SchemaStoreError) as error:
                self.store.sync_view_after_mutation(
                    "schema_one", current["revision"] + (relation == "summary"), self.saved["layoutToken"],
                    "local", "demo", "public", relation, "view",
                    "CREATE OR REPLACE VIEW public.summary AS SELECT 1", "SELECT 1", "e" * 64,
                    operation="upsert", expected_absent=False, saved_view_id="view_summary",
                )
            self.assertIn(error.exception.payload["error"]["code"], {"schema_view_changed", "schema_conflict"})

    def test_binding_rejects_revision_layout_target_and_ambiguous_view(self):
        cases = [
            (self.saved["revision"] + 1, self.saved["layoutToken"], "local", "schema_conflict"),
            (self.saved["revision"], "f" * 64, "local", "layout_conflict"),
            (self.saved["revision"], self.saved["layoutToken"], "other", "schema_target_changed"),
        ]
        for revision, token, profile, code in cases:
            with self.subTest(code=code), self.assertRaises(SchemaStoreError) as error:
                self.store.require_view_mutation_binding(
                    "schema_one", revision, token, profile, "demo", "public", "summary",
                    "upsert", {"kind": "view", "fingerprint": FINGERPRINT},
                )
            self.assertEqual(error.exception.payload["error"]["code"], code)

    def test_delete_removes_only_saved_view_and_preserves_complete_layout(self):
        before = self.store.get("schema_one")
        result = self.store.sync_view_after_mutation(
            "schema_one", self.saved["revision"], self.saved["layoutToken"],
            "local", "demo", "public", "summary", "view", None, None, None,
            operation="delete", expected_absent=False, saved_view_id="view_summary",
        )
        after = self.store.get("schema_one")
        self.assertEqual([item["id"] for item in after["schema"]["views"]], ["view_other"])
        self.assertEqual(after["schema"]["layout"], before["schema"]["layout"])
        self.assertIn("view_summary", after["schema"]["layout"]["layers"]["views"]["objects"])
        self.assertEqual(result["layoutToken"], self.saved["layoutToken"])
        self.assertEqual(after["custom"], before["custom"])

    def test_normal_save_preserves_server_owned_view_sync_receipts(self):
        result = self.store.sync_view_after_mutation(
            "schema_one", self.saved["revision"], self.saved["layoutToken"],
            "local", "demo", "public", "new_summary", "view", 'CREATE VIEW "public"."new_summary" AS SELECT 1',
            "SELECT 1", "b" * 64, operation="upsert", expected_absent=True, saved_view_id=None,
            receipt_id="ai_plan_receipt",
        )
        current = self.store.get("schema_one")
        browser_record = {key: current[key] for key in ("id", "revision", "schema", "updatedAt")}
        self.store.save("schema_one", browser_record, expected_layout_token=current["layoutToken"], layout_protocol="2")
        self.assertEqual(self.store.get("schema_one")["aiViewMutationReceipts"]["ai_plan_receipt"], result)


@unittest.skipUnless(os.environ.get("SCHEMII_TEST_PG17_DSN"), "SCHEMII_TEST_PG17_DSN is not configured")
class Postgres17ViewLockIntegrationTests(unittest.TestCase):
    def test_materialized_no_data_refresh_holds_access_exclusive_and_rolls_back(self):
        try:
            import psycopg
        except ImportError as exc:
            self.skipTest(str(exc))
        dsn = os.environ["SCHEMII_TEST_PG17_DSN"]
        setup = psycopg.connect(dsn, autocommit=True)
        locker = psycopg.connect(dsn)
        observer = psycopg.connect(dsn, autocommit=True)
        schema = "schemii_matview_lock_test"
        try:
            setup.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            setup.execute(f'CREATE SCHEMA "{schema}"')
            setup.execute(f'CREATE TABLE "{schema}".base (value integer)')
            setup.execute(f'INSERT INTO "{schema}".base VALUES (1)')
            setup.execute(f'CREATE MATERIALIZED VIEW "{schema}".target AS SELECT value FROM "{schema}".base')
            locker.execute(f'REFRESH MATERIALIZED VIEW "{schema}".target WITH NO DATA')
            locks = observer.execute("""
                SELECT l.mode FROM pg_catalog.pg_locks l
                JOIN pg_catalog.pg_class c ON c.oid = l.relation
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE l.pid = %s AND n.nspname = %s AND c.relname = 'target' AND l.granted
            """, (locker.info.backend_pid, schema)).fetchall()
            self.assertIn(("AccessExclusiveLock",), locks)
            self.assertFalse(locker.execute("SELECT relispopulated FROM pg_catalog.pg_class WHERE oid = %s::regclass", (f'{schema}.target',)).fetchone()[0])
            locker.rollback()
            self.assertTrue(observer.execute("SELECT relispopulated FROM pg_catalog.pg_class WHERE oid = %s::regclass", (f'{schema}.target',)).fetchone()[0])
        finally:
            locker.rollback()
            setup.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            for connection in (locker, observer, setup):
                connection.close()

    def test_app_and_mercury_seed_namespace_lock_identities_contend(self):
        try:
            import psycopg
        except ImportError as exc:
            self.skipTest(str(exc))
        dsn = os.environ["SCHEMII_TEST_PG17_DSN"]
        app = psycopg.connect(dsn)
        seed = psycopg.connect(dsn)
        observer = psycopg.connect(dsn, autocommit=True)
        try:
            version = int(observer.execute("SHOW server_version_num").fetchone()[0])
            self.assertGreaterEqual(version, 170000)
            self.assertLess(version, 180000)
            app.execute(
                "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext(%s)::bigint)",
                ("schemii:bookstore",),
            )
            lock = observer.execute("""
                SELECT locktype, database, classid, objid, objsubid, mode, granted
                FROM pg_catalog.pg_locks
                WHERE pid = %s AND locktype = 'advisory'
            """, (app.info.backend_pid,)).fetchone()
            self.assertEqual(lock[0], "advisory")
            self.assertIsNotNone(lock[1])
            self.assertEqual(lock[5:], ("ExclusiveLock", True))

            seed.execute("SET LOCAL lock_timeout = '200ms'")
            seed.execute("SET LOCAL statement_timeout = '2s'")
            with self.assertRaises(psycopg.errors.LockNotAvailable):
                seed.execute("""
                    SELECT pg_catalog.pg_advisory_xact_lock(
                        pg_catalog.hashtext('schemii:bookstore')::bigint
                    )
                """)
            seed.rollback()
        finally:
            app.rollback()
            seed.rollback()
            for connection in (app, seed, observer):
                connection.close()

    def test_view_read_lock_allows_base_writes_blocks_ddl_and_upgrades_without_deadlock(self):
        try:
            import psycopg
        except ImportError as exc:
            self.skipTest(str(exc))
        dsn = os.environ["SCHEMII_TEST_PG17_DSN"]
        setup = psycopg.connect(dsn, autocommit=True)
        first = psycopg.connect(dsn)
        ddl = psycopg.connect(dsn)
        writer = psycopg.connect(dsn)
        observer = psycopg.connect(dsn, autocommit=True)
        schema = "schemii_view_lock_test"
        ddl_errors = []
        try:
            version = int(setup.execute("SHOW server_version_num").fetchone()[0])
            self.assertGreaterEqual(version, 170000)
            self.assertLess(version, 180000)
            setup.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            setup.execute(f'CREATE SCHEMA "{schema}"')
            setup.execute(f'CREATE TABLE "{schema}".base (id integer)')
            setup.execute(f'CREATE VIEW "{schema}".target AS SELECT id FROM "{schema}".base')
            setup.execute(f'CREATE MATERIALIZED VIEW "{schema}".target_mat AS SELECT id FROM "{schema}".base')

            first.execute(f'SELECT * FROM "{schema}".target LIMIT 0')
            locks = observer.execute("""
                SELECT c.relname, l.mode
                FROM pg_catalog.pg_locks l
                JOIN pg_catalog.pg_class c ON c.oid = l.relation
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE l.pid = %s AND n.nspname = %s AND c.relname IN ('base', 'target')
                ORDER BY c.relname
            """, (first.info.backend_pid, schema)).fetchall()
            self.assertEqual(locks, [("base", "AccessShareLock"), ("target", "AccessShareLock")])

            writer.execute("SET LOCAL lock_timeout = '500ms'")
            writer.execute(f'INSERT INTO "{schema}".base VALUES (1)')
            writer.commit()

            def run_ddl():
                try:
                    ddl.execute("SET LOCAL lock_timeout = '4s'")
                    ddl.execute(f'ALTER VIEW "{schema}".target RENAME TO target_renamed')
                    ddl.commit()
                except Exception as error:
                    ddl_errors.append(error)
                    ddl.rollback()

            thread = threading.Thread(target=run_ddl)
            thread.start()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                waiting = observer.execute("""
                    SELECT EXISTS (
                        SELECT 1 FROM pg_catalog.pg_locks
                        WHERE pid = %s AND mode = 'AccessExclusiveLock' AND NOT granted
                    )
                """, (ddl.info.backend_pid,)).fetchone()[0]
                if waiting:
                    break
                time.sleep(0.02)
            self.assertTrue(waiting, "concurrent target DDL did not reach the lock queue")
            first.execute(f'CREATE OR REPLACE VIEW "{schema}".target AS SELECT id FROM "{schema}".base')
            first.commit()
            thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(ddl_errors, [])

            with self.assertRaises(psycopg.errors.WrongObjectType):
                first.execute(f'LOCK TABLE ONLY "{schema}".target_mat IN ACCESS SHARE MODE')
            first.rollback()
        finally:
            first.rollback()
            ddl.rollback()
            writer.rollback()
            setup.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            for connection in (first, ddl, writer, observer, setup):
                connection.close()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import copy
import json
from typing import Any

from .metadata import MetadataStore, MetadataStoreError, canonical_review_digest
from .postgres_common import ConflictError, NotFoundError, PostgresServiceError, canonical_fingerprint, postgres_error_diagnostic, quote_identifier
from .schema_store import SchemaStore, SchemaStoreError


class DurableMigrationCoordinator:
    """Coordinates reviewed PostgreSQL mutations without using process-local execution authority."""

    def __init__(self, service: Any, metadata: MetadataStore, schemas: SchemaStore):
        self.service = service
        self.metadata = metadata
        self.schemas = schemas

    def preview_full(
        self, profile_id: str, namespace: str, schema_id: str, revision: int,
        layout_token: str, allow_destructive: bool, *, source_kind: str = "normal",
    ) -> dict[str, Any]:
        profile = self.service._profile(profile_id)
        database = profile["dbname"]
        record = self.schemas.require_migration_binding(
            schema_id, revision, layout_token, profile_id, database, namespace,
        )
        preview = self.service.preview(profile_id, namespace, record["schema"], allow_destructive, persist=False)
        private = {
            "schemaBinding": {"schemaId": schema_id, "revision": revision, "layoutToken": layout_token},
            "desiredSchema": record["schema"], "steps": preview["steps"],
        }
        return self._create_plan(
            "full_schema", source_kind, "schema", schema_id, revision, layout_token,
            profile_id, database, namespace, preview["liveFingerprint"],
            canonical_fingerprint(record["schema"]), private, preview,
        )

    def preview_ai_full(
        self, operation_id: str, profile_id: str, database: str, namespace: str,
        desired_schema: dict[str, Any], allow_destructive: bool, schema_binding: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_binding(schema_binding)
        if self.service._profile(profile_id)["dbname"] != database:
            raise ConflictError("database_changed", "Connection profile database changed during preview")
        current = self.schemas.require_migration_binding(
            schema_binding["schemaId"], schema_binding["revision"], schema_binding["layoutToken"],
            profile_id, database, namespace,
        )
        if current["schema"] != desired_schema:
            raise ConflictError("schema_changed", "The server-owned desired schema does not match the requested AI preview")
        plan = self.preview_full(
            profile_id, namespace, schema_binding["schemaId"], schema_binding["revision"],
            schema_binding["layoutToken"], allow_destructive, source_kind="ai",
        )
        return {**plan, "id": None, "previewOnly": True, "applyPlanId": plan["id"]}

    def preview_view(
        self, profile_id: str, database: str, namespace: str, relation: str, operation: str,
        expectation: dict[str, Any], desired: dict[str, Any] | None, allow_destructive: bool,
        schema_binding: dict[str, Any], *, source_kind: str = "normal",
    ) -> dict[str, Any]:
        binding = self.schemas.require_view_mutation_binding(
            schema_binding["schemaId"], schema_binding["revision"], schema_binding["layoutToken"],
            profile_id, database, namespace, relation, operation, expectation, schema_binding.get("savedViewId"),
        )
        bound = {**schema_binding, "savedViewId": binding["savedViewId"]}
        preview = self.service.preview_view_mutation(
            profile_id, database, namespace, relation, operation, expectation, desired,
            allow_destructive, bound, persist=False,
        )
        private = {
            "schemaBinding": bound, "relation": relation, "operation": operation,
            "expectation": expectation, "desiredKind": desired.get("kind") if desired else None,
            "desiredDefinition": preview.get("desiredDefinition"),
            "preservation": preview.get("preservation"), "steps": preview["steps"],
        }
        live = expectation.get("fingerprint") if "fingerprint" in expectation else canonical_fingerprint({"absent": True})
        desired_fingerprint = canonical_fingerprint(desired or {"absent": True})
        return self._create_plan(
            "view_mutation", source_kind, "view", schema_binding["schemaId"], schema_binding["revision"],
            schema_binding["layoutToken"], profile_id, database, namespace, live, desired_fingerprint,
            private, preview,
        )

    def preview_insert(
        self, profile_id: str, database: str, namespace: str, relation: str,
        rows: list[dict[str, Any]], schema_binding: dict[str, Any], *, source_kind: str = "ai",
    ) -> dict[str, Any]:
        self._require_binding(schema_binding)
        self.schemas.require_migration_binding(
            schema_binding["schemaId"], schema_binding["revision"], schema_binding["layoutToken"],
            profile_id, database, namespace,
        )
        columns = list(rows[0])
        profile = self.service._profile(profile_id)
        connection = self.service._connect_profile(profile)
        try:
            self.service._execute_statement(connection, "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            target = self.service._inspect_ai_insert_target(connection, database, namespace, relation, columns)
        finally:
            try: connection.rollback()
            except Exception: pass
            self.service._close(connection)
        qualified = f"{quote_identifier(namespace)}.{quote_identifier(relation)}"
        sql = f"INSERT INTO {qualified} ({', '.join(map(quote_identifier, columns))}) SELECT {', '.join(map(quote_identifier, columns))} FROM pg_catalog.jsonb_populate_recordset(NULL::{qualified}, %s::jsonb) AS input"
        steps = [self.service._step("insert", "rows", relation, sql)]
        private = {
            "schemaBinding": schema_binding, "relation": relation, "expectation": target,
            "columns": columns, "encodedRows": json.dumps(rows, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
            "rowCount": len(rows), "steps": steps,
        }
        review = {"kind": "insert_rows", "target": {"profileId": profile_id, "database": database, "namespace": namespace, "relation": relation},
                  "columns": columns, "rows": rows, "rowCount": len(rows), "steps": steps, "warnings": [], "destructive": False}
        return self._create_plan(
            "insert_rows", source_kind, "schema", schema_binding["schemaId"], schema_binding["revision"],
            schema_binding["layoutToken"], profile_id, database, namespace, target["fingerprint"],
            canonical_fingerprint({"columns": columns, "rows": rows}), private, review,
        )

    def apply(
        self, plan_id: str, review_digest: str, confirm_destructive: bool,
        *, expected_profile_id: str | None = None,
    ) -> dict[str, Any]:
        if expected_profile_id is not None:
            routed = self.metadata.get_migration_plan(plan_id)
            if routed["target"]["profileId"] != expected_profile_id:
                raise NotFoundError("Migration plan was not found for this profile")
        authorization = self.metadata.create_migration_execution(plan_id, review_digest, confirm_destructive)
        if not authorization["executionOwner"]:
            return self.status(plan_id)
        execution_id = authorization["executionId"]
        plan = self.metadata.get_migration_plan(plan_id, include_private=True)
        private = plan["privatePayload"]
        binding = private["schemaBinding"]
        try:
            if plan["adapterKind"] == "view_mutation":
                self.schemas.require_view_mutation_binding(
                    binding["schemaId"], binding["revision"], binding["layoutToken"], plan["target"]["profileId"],
                    plan["target"]["databaseName"], plan["target"]["namespaceName"], private["relation"],
                    private["operation"], private["expectation"], binding.get("savedViewId"),
                )
            else:
                self.schemas.require_migration_binding(
                    binding["schemaId"], binding["revision"], binding["layoutToken"], plan["target"]["profileId"],
                    plan["target"]["databaseName"], plan["target"]["namespaceName"],
                )
        except SchemaStoreError as exc:
            self.metadata.fail_migration_execution_before_mutation(execution_id, exc.payload["error"])
            raise
        profile = self.service._profile(plan["target"]["profileId"])
        if self.service._profile_fingerprint(profile) != plan["target"]["profileFingerprint"]:
            self.metadata.fail_migration_execution_before_mutation(execution_id, {"code": "profile_changed"})
            raise ConflictError("profile_changed", "Connection profile changed after preview")
        try:
            connection = self.service._connect_profile(profile)
        except Exception as exc:
            self.metadata.fail_migration_execution_before_mutation(execution_id, {"code": "target_connect_failed"})
            raise PostgresServiceError(502, "target_connect_failed", "PostgreSQL target could not be connected; the execution was not started") from exc
        return self._execute(plan, execution_id, connection)

    def status(self, plan_id: str) -> dict[str, Any]:
        aggregate = self.metadata.get_migration_status(plan_id)
        return self._public_status(aggregate)

    def execution_status(self, execution_id: str) -> dict[str, Any]:
        return self._public_status(self.metadata.get_migration_execution_context(execution_id))

    def reconcile(self, execution_id: str) -> dict[str, Any]:
        context = self.metadata.get_migration_execution_context(execution_id)
        execution, plan = context["execution"], context["plan"]
        if execution["state"] == "succeeded" and execution["commitOutcome"] == "committed" and (
            execution.get("sync") is None or execution["sync"]["state"] == "pending"
        ):
            self._sync(plan, execution_id, None)
            return self.execution_status(execution_id)
        if execution["state"] == "ready":
            self.metadata.fail_migration_execution_before_mutation(
                execution_id, {"code": "interrupted_before_target_transaction"},
            )
            return self.execution_status(execution_id)
        if execution["state"] == "applying":
            self.metadata.prepare_migration_reconciliation(
                execution_id, {"code": "interrupted_applying_execution", "reconcileOnly": True},
            )
            context = self.metadata.get_migration_execution_context(execution_id)
            execution, plan = context["execution"], context["plan"]
        if execution["state"] != "uncertain":
            return self._public_status(context)
        if execution["reconciliationStatus"] == "failed":
            return self._public_status(context)
        profile = self.service._profile(plan["target"]["profileId"])
        if self.service._profile_fingerprint(profile) != plan["target"]["profileFingerprint"]:
            raise ConflictError("profile_changed", "Connection profile changed; transaction status cannot be reconciled")
        connection = self.service._connect_profile(profile)
        try:
            identity = self._target_identity(connection)
            if canonical_fingerprint(identity) != plan["target"]["connectedTargetFingerprint"]:
                raise ConflictError("target_changed", "Connected PostgreSQL target does not match the durable execution")
            rows = self.service._execute_rows(connection, "SELECT pg_catalog.pg_xact_status(%s::xid8) AS status", (execution["targetXid"],))
        finally:
            try: connection.rollback()
            except Exception: pass
            self.service._close(connection)
        xid_status = rows[0].get("status") if rows else None
        if xid_status not in {"committed", "aborted"}:
            raise PostgresServiceError(503, "execution_outcome_unknown", "PostgreSQL transaction outcome remains uncertain")
        outcome = "committed" if xid_status == "committed" else "rolled_back"
        if outcome == "committed" and execution.get("intendedResult") is None:
            self.metadata.require_manual_migration_reconciliation(
                execution_id,
                {"code": "committed_without_intended_result", "xidStatus": xid_status, "targetIdentity": identity},
            )
            return self.execution_status(execution_id)
        self.metadata.reconcile_migration_execution(execution_id, outcome, {"xidStatus": xid_status, "targetIdentity": identity})
        if outcome == "committed":
            self._sync(plan, execution_id, None)
        return self.execution_status(execution_id)

    def _create_plan(
        self, adapter: str, source: str, resource_kind: str, resource_id: str, revision: int,
        layout_token: str, profile_id: str, database: str, namespace: str, live_fingerprint: str,
        desired_fingerprint: str, private: dict[str, Any], preview: dict[str, Any],
    ) -> dict[str, Any]:
        target_identity = self._preview_target_identity(profile_id, database)
        review = {
            "adapterKind": adapter,
            "target": {"profileId": profile_id, "database": database, "namespace": namespace},
            "steps": copy.deepcopy(preview.get("steps", [])), "warnings": copy.deepcopy(preview.get("warnings", [])),
            "destructive": bool(preview.get("destructive")),
        }
        for key in ("kind", "columns", "rows", "rowCount", "operation", "relation"):
            if key in preview:
                review[key] = copy.deepcopy(preview[key])
        digest = canonical_review_digest(review)
        created = self.metadata.create_migration_plan(
            "schemii", resource_kind, resource_id, revision, layout_token,
            {"profileId": profile_id, "databaseName": database, "namespaceName": namespace,
             "profileFingerprint": self.service._profile_fingerprint(self.service._profile(profile_id)),
             "connectedTargetFingerprint": canonical_fingerprint(target_identity)},
            live_fingerprint, desired_fingerprint, private, review, digest, review["destructive"],
            adapter_kind=adapter, source_kind=source, ttl_seconds=self.service._plan_ttl,
        )
        return {"id": created["planId"], "reviewDigest": digest, **review}

    def _preview_target_identity(self, profile_id: str, database: str) -> dict[str, Any]:
        connection = self.service._connect_profile(self.service._profile(profile_id))
        try:
            identity = self._target_identity(connection)
            if identity["database"] != database:
                raise ConflictError("database_changed", "Connected PostgreSQL database does not match the requested database")
            return identity
        finally:
            try: connection.rollback()
            except Exception: pass
            self.service._close(connection)

    def _target_identity(self, connection: Any) -> dict[str, Any]:
        rows = self.service._execute_rows(connection, """
            SELECT current_database() AS database,
                   (SELECT oid::text FROM pg_catalog.pg_database WHERE datname = current_database()) AS database_oid,
                   current_setting('server_version_num') AS server_version_num,
                   pg_catalog.inet_server_addr()::text AS server_address,
                   pg_catalog.inet_server_port() AS server_port
        """)
        if len(rows) != 1 or not rows[0].get("database") or not rows[0].get("database_oid"):
            raise PostgresServiceError(502, "target_identity_unavailable", "PostgreSQL target identity could not be verified")
        return {"database": rows[0]["database"], "databaseOid": str(rows[0]["database_oid"]),
                "serverVersionNum": str(rows[0]["server_version_num"]),
                "serverAddress": rows[0].get("server_address"), "serverPort": rows[0].get("server_port")}

    def _execute(self, plan: dict[str, Any], execution_id: str, connection: Any) -> dict[str, Any]:
        cursor = connection.cursor()
        applying = False
        commit_requested = False
        refreshed = None
        try:
            cursor.execute("BEGIN")
            private = plan["privatePayload"]
            self.service._acquire_namespace_mutation_lock(cursor, plan["target"]["namespaceName"], plan["target"]["databaseName"])
            self._stale_check(plan, private, connection, cursor)
            identity = self._target_identity(connection)
            if canonical_fingerprint(identity) != plan["target"]["connectedTargetFingerprint"]:
                raise ConflictError("target_changed", "Connected PostgreSQL target changed after preview")
            rows = self.service._execute_rows(connection, "SELECT pg_catalog.pg_current_xact_id()::text AS xid")
            xid = rows[0].get("xid") if rows else None
            if not isinstance(xid, str) or not xid:
                raise PostgresServiceError(502, "transaction_receipt_unavailable", "PostgreSQL transaction receipt is unavailable")
            self.metadata.begin_migration_execution(execution_id, xid, identity)
            applying = True
            owner = (private.get("preservation") or {}).get("owner")
            if owner and private.get("operation") == "upsert":
                cursor.execute(f"SET LOCAL ROLE {quote_identifier(owner)}")
            for step in private["steps"]:
                if plan["adapterKind"] == "insert_rows":
                    cursor.execute(step["sql"], (private["encodedRows"],))
                else:
                    cursor.execute(step["sql"])
            if owner and private.get("operation") == "upsert":
                cursor.execute("RESET ROLE")
            intended, refreshed = self._intended_result(plan, private, connection, cursor)
            self.metadata.record_migration_intended_result(execution_id, intended)
            commit_requested = True
            try:
                connection.commit()
            except Exception as exc:
                try:
                    self.metadata.finish_migration_execution(execution_id, "uncertain", "uncertain", evidence={"code": "commit_acknowledgement_lost", "postgres": postgres_error_diagnostic(exc)})
                except MetadataStoreError:
                    pass
                raise self._recovery_error(execution_id, "PostgreSQL commit acknowledgement was lost") from exc
            try:
                self.metadata.finish_migration_execution(execution_id, "succeeded", "committed", evidence={"targetIdentity": identity})
            except MetadataStoreError as exc:
                raise self._recovery_error(execution_id, "PostgreSQL committed before metadata finalization completed") from exc
        except PostgresServiceError:
            if commit_requested:
                raise
            self._rollback_failure(connection, plan, execution_id, applying)
            raise
        except MetadataStoreError:
            if commit_requested:
                raise self._recovery_error(execution_id, "PostgreSQL commit outcome requires durable reconciliation")
            self._rollback_failure(connection, plan, execution_id, applying)
            raise
        except Exception as exc:
            if commit_requested:
                raise
            self._rollback_failure(connection, plan, execution_id, applying)
            raise PostgresServiceError(422, "apply_failed", "PostgreSQL mutation failed and was rolled back", {"postgres": postgres_error_diagnostic(exc)}) from exc
        finally:
            close = getattr(cursor, "close", None)
            if close: close()
            self.service._close(connection)
        self._sync(plan, execution_id, refreshed)
        try:
            return self.execution_status(execution_id)
        except MetadataStoreError as exc:
            raise PostgresServiceError(
                503, "post_commit_status_unavailable",
                "PostgreSQL committed, but durable synchronization status is temporarily unavailable",
                {"executionId": execution_id, "committed": True, "reconcileRequired": True},
            ) from exc

    def _rollback_failure(self, connection: Any, plan: dict[str, Any], execution_id: str, applying: bool) -> None:
        try:
            connection.rollback()
        except Exception as exc:
            if applying:
                self.metadata.finish_migration_execution(execution_id, "uncertain", "uncertain", evidence={"code": "rollback_acknowledgement_lost"})
                raise PostgresServiceError(500, "execution_outcome_unknown", "PostgreSQL rollback could not be proven; reconcile without replay") from exc
            raise
        evidence = {"code": "validation_or_mutation_failed"}
        if applying:
            self.metadata.finish_migration_execution(execution_id, "failed", "rolled_back", evidence=evidence)
        else:
            self.metadata.fail_migration_execution_before_mutation(execution_id, evidence)

    @staticmethod
    def _recovery_error(execution_id: str, message: str) -> PostgresServiceError:
        return PostgresServiceError(
            503, "execution_outcome_unknown", f"{message}; reconcile without replay",
            {"executionId": execution_id, "reconcileRequired": True},
        )

    def _stale_check(self, plan: dict[str, Any], private: dict[str, Any], connection: Any, cursor: Any) -> None:
        adapter = plan["adapterKind"]
        if adapter == "full_schema":
            current = self.service._introspect_connection(connection, plan["target"]["profileId"], plan["target"]["namespaceName"])
            if current["postgres"]["fingerprint"] != plan["liveFingerprint"]:
                raise ConflictError("stale_plan", "Database schema changed after preview")
            return
        if adapter == "insert_rows":
            relation = private["relation"]
            qualified = f"{quote_identifier(plan['target']['namespaceName'])}.{quote_identifier(relation)}"
            cursor.execute(f"LOCK TABLE {qualified} IN ROW EXCLUSIVE MODE")
            current = self.service._inspect_ai_insert_target(connection, plan["target"]["databaseName"], plan["target"]["namespaceName"], relation, private["columns"])
            if current["fingerprint"] != plan["liveFingerprint"]:
                raise ConflictError("relation_changed", "The insert target changed after preview")
            return
        expectation = private["expectation"]
        relation = private["relation"]
        qualified = f"{quote_identifier(plan['target']['namespaceName'])}.{quote_identifier(relation)}"
        if "fingerprint" in expectation:
            cursor.execute(f"SELECT * FROM {qualified} LIMIT 0")
        try:
            current = self.service._inspect_relation_connection(connection, plan["target"]["profileId"], plan["target"]["databaseName"], plan["target"]["namespaceName"], relation, None, None)
        except NotFoundError:
            current = None
        if expectation == {"absent": True}:
            if current is not None:
                raise ConflictError("relation_changed", "The expected-absent PostgreSQL relation now exists")
        elif current is None or current["kind"] != expectation["kind"] or current["fingerprint"] != expectation["fingerprint"]:
            raise ConflictError("relation_changed", "The PostgreSQL relation changed after preview")
        preservation = private.get("preservation")
        if preservation and current:
            latest = self.service._view_recreation_preservation(connection, plan["target"]["namespaceName"], relation)
            if latest["fingerprint"] != preservation["fingerprint"]:
                raise ConflictError("relation_changed", "Materialized view metadata changed after preview")

    def _intended_result(self, plan: dict[str, Any], private: dict[str, Any], connection: Any, cursor: Any) -> tuple[dict[str, Any], Any]:
        adapter = plan["adapterKind"]
        base = {"planId": plan["planId"], "adapterKind": adapter,
                "target": {"profileId": plan["target"]["profileId"], "database": plan["target"]["databaseName"], "namespace": plan["target"]["namespaceName"]}}
        if adapter == "full_schema":
            refreshed = self.service._introspect_connection(connection, plan["target"]["profileId"], plan["target"]["namespaceName"])
            return {**base, "kind": "migration_applied", "sourceFingerprint": plan["liveFingerprint"], "resultFingerprint": refreshed["postgres"]["fingerprint"], "destructive": plan["destructive"]}, refreshed
        if adapter == "insert_rows":
            count = getattr(cursor, "rowcount", private["rowCount"])
            if not isinstance(count, int) or count < 0: count = private["rowCount"]
            return {**base, "kind": "rows_inserted", "relation": private["relation"], "insertedRowCount": count}, None
        relation = private["relation"]
        if private["operation"] == "delete":
            intended = {**base, "kind": "view_deleted", "operation": "delete", "relation": relation, "deletedKind": private["expectation"]["kind"]}
        else:
            descriptor = self.service._inspect_relation_connection(connection, plan["target"]["profileId"], plan["target"]["databaseName"], plan["target"]["namespaceName"], relation, private["desiredKind"], None)
            intended = {**base, "kind": "view_mutated", "operation": "upsert", "relation": relation, "descriptor": descriptor,
                        "desiredDefinition": private["desiredDefinition"], "queryDefinition": descriptor.get("definition", {}).get("sql")}
        return intended, None

    def _sync(self, plan: dict[str, Any], execution_id: str, refreshed: Any) -> None:
        try:
            self.metadata.record_migration_sync(execution_id, "pending")
            private = plan["privatePayload"]
            binding = private["schemaBinding"]
            if plan["adapterKind"] == "full_schema":
                if refreshed is None:
                    refreshed = self.service.introspect(plan["target"]["profileId"], plan["target"]["namespaceName"])
                intended = self.metadata.get_migration_execution(execution_id)["intendedResult"]
                if refreshed["postgres"]["fingerprint"] != intended["resultFingerprint"]:
                    raise SchemaStoreError(409, "schema_target_changed", "PostgreSQL changed after the committed migration")
                receipt = self.schemas.sync_full_migration_result(binding["schemaId"], binding["revision"], binding["layoutToken"], refreshed, execution_id)
            elif plan["adapterKind"] == "view_mutation":
                intended = self.metadata.get_migration_execution(execution_id)["intendedResult"]
                descriptor = intended.get("descriptor")
                if descriptor is not None:
                    try:
                        current = self.service.inspect_relation(
                            plan["target"]["profileId"], plan["target"]["databaseName"],
                            plan["target"]["namespaceName"], private["relation"], descriptor["kind"],
                        )
                    except PostgresServiceError as exc:
                        raise SchemaStoreError(409, "schema_target_changed", "Committed view is no longer present for synchronization") from exc
                    if current.get("fingerprint") != descriptor.get("fingerprint"):
                        raise SchemaStoreError(409, "schema_target_changed", "Committed view changed before saved-schema synchronization")
                else:
                    try:
                        self.service.inspect_relation(
                            plan["target"]["profileId"], plan["target"]["databaseName"],
                            plan["target"]["namespaceName"], private["relation"],
                        )
                    except NotFoundError:
                        pass
                    else:
                        raise SchemaStoreError(409, "schema_target_changed", "Deleted view identity exists again before synchronization")
                receipt = self.schemas.sync_view_after_mutation(
                    binding["schemaId"], binding["revision"], binding["layoutToken"], plan["target"]["profileId"],
                    plan["target"]["databaseName"], plan["target"]["namespaceName"], private["relation"],
                    descriptor.get("kind") if descriptor else None, intended.get("desiredDefinition"), intended.get("queryDefinition"),
                    descriptor.get("fingerprint") if descriptor else None, operation=private["operation"],
                    expected_absent=private["expectation"] == {"absent": True}, saved_view_id=binding.get("savedViewId"), receipt_id=execution_id,
                )
            else:
                receipt = {"status": "not_required"}
            self.metadata.record_migration_sync(execution_id, "succeeded", receipt=receipt)
        except SchemaStoreError as exc:
            state = "conflict" if exc.status == 409 else "failed"
            self.metadata.record_migration_sync(execution_id, state, receipt=exc.payload["error"])
        except Exception as exc:
            try:
                self.metadata.record_migration_sync(execution_id, "failed", receipt={"code": "sync_failed", "message": "Saved resource synchronization failed"})
            except Exception:
                pass

    @staticmethod
    def _require_binding(binding: Any) -> None:
        if not isinstance(binding, dict) or set(binding) != {"schemaId", "revision", "layoutToken"}:
            raise ConflictError("invalid_schema_binding", "Schema binding is invalid")

    @staticmethod
    def _public_status(context: dict[str, Any]) -> dict[str, Any]:
        plan = context["plan"]
        execution = context.get("execution")
        result = {"planId": plan["planId"], "adapterKind": plan["adapterKind"], "reviewDigest": plan["reviewDigest"],
                  "state": plan["state"] if execution is None else execution["state"], "execution": execution,
                  "review": plan["reviewPayload"]}
        if execution is not None and execution["state"] == "uncertain" and execution.get("reconciliationStatus") == "failed":
            result["recovery"] = {
                "status": "manual_required", "code": "insufficient_execution_evidence",
                "message": "Transaction evidence is insufficient for automatic success; inspect the target and saved resource manually",
            }
        elif execution is not None and execution["state"] == "applying":
            result["recovery"] = {
                "status": "reconcile_required", "code": "interrupted_applying_execution",
                "message": "Execution has durable transaction evidence; reconcile without replay",
            }
        elif execution is not None and execution["state"] == "succeeded" and (
            execution.get("sync") is None or execution["sync"]["state"] == "pending"
        ):
            result["recovery"] = {
                "status": "sync_pending", "code": "saved_resource_sync_required",
                "message": "PostgreSQL committed; reconcile to finish saved-resource synchronization without replay",
            }
        return result


__all__ = ["DurableMigrationCoordinator"]

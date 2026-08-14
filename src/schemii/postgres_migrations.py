from __future__ import annotations

from typing import Any

from .postgres_common import NotFoundError, PostgresServiceError


class PostgresMigrationFacade:
    """Compatibility-shaped facade over the one durable migration coordinator."""

    def __init__(self):
        self._coordinator = None

    def set_coordinator(self, coordinator: Any) -> None:
        self._coordinator = coordinator

    def _require(self):
        if self._coordinator is None:
            raise PostgresServiceError(503, "durable_migrations_unavailable", "Migration plans require durable metadata")
        return self._coordinator

    def preview_ai_migration(self, operation_id, profile_id, database, namespace, desired_schema, allow_destructive, schema_binding):
        return self._require().preview_ai_full(operation_id, profile_id, database, namespace, desired_schema, allow_destructive, schema_binding)

    def preview_ai_insert_rows(self, profile_id, database, namespace, relation, rows, schema_binding):
        plan = self._require().preview_insert(profile_id, database, namespace, relation, rows, schema_binding)
        return {**plan, "id": None, "previewOnly": True, "applyPlanId": plan["id"], "planDigest": plan["reviewDigest"]}

    def preview_ai_create_view(self, profile_id, database, namespace, relation, definition, schema_binding):
        plan = self._require().preview_view(profile_id, database, namespace, relation, "upsert", {"absent": True}, {"kind": "view", "definition": definition}, False, schema_binding, source_kind="ai")
        return {**plan, "id": None, "previewOnly": True, "applyPlanId": plan["id"], "planDigest": plan["reviewDigest"], "kind": "create_view"}

    def apply_ai_migration(self, plan_id, profile_id, expected_destructive, confirm_destructive):
        coordinator = self._require()
        status = coordinator.status(plan_id)
        if status["review"]["destructive"] != expected_destructive:
            raise NotFoundError("AI migration plan was not found or has expired")
        return coordinator.apply(plan_id, status["reviewDigest"], confirm_destructive, expected_profile_id=profile_id)

    def apply_ai_postgres_write(self, plan_id, profile_id, expected_kind, review_digest):
        coordinator = self._require()
        status = coordinator.status(plan_id)
        if status["adapterKind"] != ("insert_rows" if expected_kind == "insert_rows" else "view_mutation"):
            raise NotFoundError("AI write plan was not found or has expired")
        return coordinator.apply(plan_id, review_digest, True, expected_profile_id=profile_id)

    def reconcile(self, plan_id):
        coordinator = self._require()
        status = coordinator.status(plan_id)
        execution = status.get("execution")
        if execution and (
            execution["state"] in {"ready", "applying", "uncertain"}
            or (execution["state"] == "succeeded" and (execution.get("sync") is None or execution["sync"]["state"] == "pending"))
        ):
            return coordinator.reconcile(execution["executionId"])
        return status

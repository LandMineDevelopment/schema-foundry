from __future__ import annotations

import uuid
from typing import Any, Callable

from .ai_http import bounded_ai_query_result
from .ai_schema_mutations import apply_schema_actions
from .ai_tool_contracts import contract_for_action
from .metadata import MetadataStoreError
from .opencode_service import OpenCodeServiceError
from .postgres_common import PostgresServiceError
from .schema_store import SchemaStoreError


class SchemiiAiExecutor:
    """Executes and reconciles Schemii proposals outside the HTTP adapter."""

    def __init__(self, service, store, authority, *, mutation_types, has_access, policy_binding):
        self.service = service
        self.store = store
        self.authority = authority
        self.mutation_types = mutation_types
        self.has_access = has_access
        self.policy_binding = policy_binding

    def reconcile(self, chat: dict[str, Any], current: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
        action = proposal["action"]
        try:
            if action.get("type") == "migration_apply":
                result = self.service.reconcile_ai_migration(action["planId"], action["profileId"])
            elif action.get("type") == "postgres_write_apply":
                result = self.service.reconcile_ai_postgres_write(action["planId"], action["profileId"])
            elif action.get("type") in self.mutation_types | {"schema_batch"}:
                result = self.store.get(chat["schemaId"]).get("aiOperationReceipts", {}).get(current["id"])
                if result is None:
                    raise SchemaStoreError(409, "operation_not_applied", "No saved-design receipt exists for this operation")
            elif action.get("type") == "create_project":
                result = next((item.get("aiOperationReceipts", {}).get(current["id"]) for item in self.store.list() if current["id"] in item.get("aiOperationReceipts", {})), None)
                if result is None:
                    raise SchemaStoreError(409, "operation_not_applied", "No created-project receipt exists for this operation")
            else:
                return {"operation": current}
        except (PostgresServiceError, SchemaStoreError) as error:
            if isinstance(error, SchemaStoreError):
                error_payload, error_status = error.payload["error"], error.status
                error_code = error_payload["code"]
            else:
                error_payload, error_status, error_code = error.to_dict()["error"], error.status, error.code
            terminal = {"apply_not_committed", "profile_changed", "database_changed", "plan_consumed", "not_found", "relation_changed"}
            state = "uncertain" if error_code == "execution_outcome_unknown" else "failed" if error_code in terminal or error_status < 500 else "uncertain"
            resolved = current if state == "uncertain" else self.authority.resolve_operation(current["id"], chat["id"], state, error=error_payload)
        else:
            durable_state = result.get("state") if action.get("type") in {"migration_apply", "postgres_write_apply"} and isinstance(result, dict) else None
            if durable_state in {"ready", "applying", "uncertain"}:
                return {"operation": current, "migrationExecution": result}
            if durable_state == "failed":
                resolved = self.authority.resolve_operation(current["id"], chat["id"], "failed", error={"code": "apply_not_committed", "message": "PostgreSQL execution did not commit; create a fresh preview"})
            else:
                resolved = self.authority.resolve_operation(current["id"], chat["id"], "succeeded", result=result)
        return {"operation": resolved}

    def execute(self, action, session_id, schema_id, record, profile, authorization_target, schema_concurrency, operation_id, access, *, session_binding, server_id, console_policy, proposal_envelope):
        service, store, authority = self.service, self.store, self.authority
        action_type = action.get("type") or action.get("action")
        contract = contract_for_action("schemii", action)
        if contract is not None and contract.executor_adapter != action_type:
            raise OpenCodeServiceError(500, "ai_contract_error", "Schemii AI tool executor contract is invalid")
        schema_binding = {"schemaId": schema_id, **schema_concurrency}
        if action_type == "schema_read_query":
            if profile is None or action.get("profileId") != authorization_target.get("profileId") or action.get("namespace") != authorization_target.get("namespace"):
                raise PostgresServiceError(409, "action_target_changed", "Query target no longer matches the proposal")
            result = service.execute_read_only_sql(profile["id"], authorization_target["namespace"], action.get("sql"), database=profile.get("dbname"), expected_profile_fingerprint=service.profile_context_fingerprint(profile["id"]), allow_explain=False, max_rows=100, max_columns=50, max_result_bytes=256 * 1024)
            reference = authority.create_result(session_id, {"resource": schema_id, "target": authorization_target, "revision": record["revision"], "access": "data"}, bounded_ai_query_result(result, max_rows=50, max_columns=50, max_bytes=24 * 1024))
            return {"kind": "sql_result", "display": result, "resultRef": reference["id"], "schemaConcurrency": schema_concurrency, "authorizationTarget": authorization_target}
        if action_type == "data_read":
            if not self.has_access(access, "structured") or profile is None or action.get("profileId") != authorization_target.get("profileId") or action.get("namespace") != authorization_target.get("namespace"):
                raise PostgresServiceError(409, "action_target_changed", "Structured data-read target no longer matches the proposal")
            result = service.preview_table_data(profile["id"], action["namespace"], action["relation"], action["offset"], action["limit"])
            names = [column["name"] for column in result["columns"]]
            display = {"columns": [{"name": name} for name in names], "rows": [[row.get(name) for name in names] for row in result["rows"]], "rowCount": len(result["rows"]), "truncated": result["hasMore"]}
            reference = authority.create_result(session_id, {"resource": schema_id, "target": authorization_target, "revision": record["revision"], "access": "data"}, bounded_ai_query_result(display, max_rows=50, max_columns=50, max_bytes=24 * 1024))
            return {"kind": "data_result", "display": display, "resultRef": reference["id"], "schemaConcurrency": schema_concurrency, "authorizationTarget": authorization_target}
        if action_type == "raw_write":
            if not self.has_access(access, "rawwrite") or profile is None or action.get("profileId") != authorization_target.get("profileId") or action.get("namespace") != authorization_target.get("namespace"):
                raise PostgresServiceError(409, "action_target_changed", "Raw-write target no longer matches the proposal")
            console_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"schemii-ai-console:{operation_id}"))
            execution_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"schemii-ai-execution:{operation_id}"))
            grant = service.create_console_write_grant(profile["id"], {"consoleId": console_id, "database": profile["dbname"], "namespace": action["namespace"], "confirmed": True}, session_binding, server_id)
            try:
                result = service.execute_console(profile["id"], {"executionId": execution_id, "consoleId": console_id, "database": profile["dbname"], "namespace": action["namespace"], "sql": action["sql"], "mode": "write", "writeGrantId": grant["writeGrantId"]}, session_binding, server_id, console_policy)
            finally:
                try:
                    service.revoke_console_write_grant(profile["id"], grant["writeGrantId"], session_binding, server_id)
                except PostgresServiceError:
                    pass
            return {"kind": "raw_sql_result", "mode": "write", "execution": result, "schemaConcurrency": schema_concurrency, "authorizationTarget": authorization_target}
        if action_type == "migration_preview":
            selected = next((item for item in service.list_profiles() if item.get("id") == action.get("profileId")), None)
            if selected is None or selected.get("dbname") != action.get("database") or service.profile_context_fingerprint(selected["id"]) != action.get("profileFingerprint") or any(action.get(key) != authorization_target.get(key) for key in ("profileId", "database", "namespace", "profileFingerprint")):
                raise PostgresServiceError(409, "action_target_changed", "Migration target no longer matches the proposal")
            saved_target = record["schema"].get("postgres", {})
            if (saved_target.get("sourceProfileId"), saved_target.get("database"), saved_target.get("namespace")) != (selected["id"], selected["dbname"], action["namespace"]):
                raise SchemaStoreError(409, "schema_target_changed", "Saved schema target no longer matches the migration target")
            plan = service.preview_ai_migration(operation_id, selected["id"], selected["dbname"], action["namespace"], record["schema"], action.get("destructivePolicy") == "allow-preview", schema_binding)
            apply_action = {"type": "migration_apply", "profileId": selected["id"], "database": selected["dbname"], "namespace": action["namespace"], "planId": plan["applyPlanId"], "destructive": plan["destructive"], "reviewDigest": plan["reviewDigest"], "requiresConfirmation": True}
            current_chat = authority.get_chat(session_id)
            proposal = authority.create_proposal(session_id, apply_action, self.policy_binding(current_chat, apply_action, origin="server_apply"), authorization_target, schema_concurrency)
            return {"kind": "migration_plan", "plan": plan, "target": {"profileId": selected["id"], "database": selected["dbname"], "namespace": action["namespace"], "profileFingerprint": action["profileFingerprint"]}, "schemaBinding": schema_binding, "applyProposal": proposal_envelope(proposal, session_id, current_chat)}
        if action_type in {"insert_rows_preview", "create_view_preview"}:
            selected = next((item for item in service.list_profiles() if item.get("id") == action.get("profileId")), None)
            if selected is None or selected.get("dbname") != action.get("database") or service.profile_context_fingerprint(selected["id"]) != action.get("profileFingerprint") or any(action.get(key) != authorization_target.get(key) for key in ("profileId", "database", "namespace", "profileFingerprint")):
                raise PostgresServiceError(409, "action_target_changed", "PostgreSQL write target no longer matches the proposal")
            if action_type == "create_view_preview":
                store.require_view_mutation_binding(schema_id, schema_concurrency["revision"], schema_concurrency["layoutToken"], selected["id"], selected["dbname"], action["namespace"], action["relation"], "upsert", {"absent": True}, None)
                plan = service.preview_ai_create_view(operation_id, selected["id"], selected["dbname"], action["namespace"], action["relation"], action["definition"], schema_binding)
                write_kind = "create_view"
            else:
                plan = service.preview_ai_insert_rows(operation_id, selected["id"], selected["dbname"], action["namespace"], action["relation"], action["rows"], schema_binding)
                write_kind = "insert_rows"
            apply_action = {"type": "postgres_write_apply", "writeKind": write_kind, "profileId": selected["id"], "database": selected["dbname"], "namespace": action["namespace"], "relation": action["relation"], "planId": plan["applyPlanId"], "reviewDigest": plan["planDigest"], "rowCount": plan.get("rowCount"), "reviewedPlan": plan, "requiresConfirmation": True}
            current_chat = authority.get_chat(session_id)
            proposal = authority.create_proposal(session_id, apply_action, self.policy_binding(current_chat, apply_action, origin="server_apply"), authorization_target, schema_concurrency)
            return {"kind": "postgres_write_plan", "writeKind": write_kind, "plan": plan, "target": {"profileId": selected["id"], "database": selected["dbname"], "namespace": action["namespace"], "relation": action["relation"], "profileFingerprint": action["profileFingerprint"]}, "schemaBinding": schema_binding, "applyProposal": proposal_envelope(proposal, session_id, current_chat)}
        if action_type == "migration_apply":
            return service.apply_ai_migration(operation_id, action["planId"], action["profileId"], action["database"], action["namespace"], action["destructive"], True)
        if action_type == "postgres_write_apply":
            if profile is None or any(action.get(key) != authorization_target.get(key) for key in ("profileId", "database", "namespace")):
                raise PostgresServiceError(409, "action_target_changed", "PostgreSQL write target no longer matches the reviewed plan")
            return service.apply_ai_postgres_write(operation_id, action["planId"], action["profileId"], action["database"], action["namespace"], action["relation"], action["writeKind"], action["reviewDigest"])
        if action_type == "open_project":
            target = store.get(action.get("schemaId"))
            if target["schema"].get("projectName") != action.get("projectName"):
                raise SchemaStoreError(409, "schema_conflict", "Target project changed; request a fresh proposal")
            return {"kind": "client_command", "command": {"type": "open_schema", "schemaId": target["id"], "revision": target["revision"], "layoutToken": target["layoutToken"]}}
        if action_type == "connection_setup":
            fields = {key: action.get(key) for key in ("name", "host", "port", "database", "user", "sslmode")}
            if not all(value is not None for value in fields.values()):
                raise OpenCodeServiceError(400, "validation_error", "Connection proposal is incomplete")
            return {"kind": "client_command", "command": {"type": "prefill_postgres_profile", "profile": fields}}
        if action_type == "open_connection":
            selected = next((item for item in service.list_profiles() if item.get("id") == action.get("profileId")), None)
            if selected is None or (selected.get("name"), selected.get("dbname"), service.profile_context_fingerprint(selected["id"])) != (action.get("name"), action.get("database"), action.get("profileFingerprint")):
                raise PostgresServiceError(409, "action_target_changed", "Saved connection no longer matches the proposal")
            if action["namespace"] not in service.list_namespaces(selected["id"]):
                raise PostgresServiceError(409, "action_target_changed", "PostgreSQL namespace no longer exists")
            return {"kind": "client_command", "command": {"type": "select_postgres_profile", "profileId": selected["id"], "name": selected["name"], "database": selected["dbname"], "namespace": action["namespace"], "profileFingerprint": action["profileFingerprint"]}}
        if action_type == "create_project":
            return store.create_ai_project(operation_id, action["projectName"])
        if action_type in self.mutation_types | {"schema_batch"}:
            actions = action.get("actions") if action_type == "schema_batch" else [action]
            if action_type == "schema_batch" and (not isinstance(actions, list) or not 2 <= len(actions) <= 5 or any(not isinstance(item, dict) or item.get("type") not in self.mutation_types for item in actions)):
                raise OpenCodeServiceError(400, "validation_error", "Schema batch is invalid")
            for item in actions:
                if item.get("profileId") is not None:
                    postgres = record["schema"].get("postgres", {})
                    if (item["profileId"], item["namespace"]) != (postgres.get("sourceProfileId"), postgres.get("namespace")):
                        raise SchemaStoreError(409, "schema_target_changed", "Saved schema target no longer matches the proposal")
            receipt = store.apply_ai_mutation(schema_id, operation_id, schema_concurrency["revision"], schema_concurrency["layoutToken"], lambda current: apply_schema_actions(current, actions, operation_id))
            return self._add_migration_preview(receipt, session_id, operation_id, authorization_target, proposal_envelope)
        raise OpenCodeServiceError(409, "action_temporarily_unavailable", "This action is unavailable until its server execution adapter is installed")

    def _add_migration_preview(self, receipt, session_id, operation_id, authorization_target, proposal_envelope):
        if not authorization_target:
            return receipt
        saved = self.store.get(receipt["schemaId"])
        target = saved["schema"].get("postgres", {})
        if (target.get("sourceProfileId"), target.get("database"), target.get("namespace")) != (authorization_target["profileId"], authorization_target["database"], authorization_target["namespace"]):
            return receipt
        binding = {"schemaId": receipt["schemaId"], "revision": receipt["revision"], "layoutToken": receipt["layoutToken"]}
        try:
            plan = self.service.preview_ai_migration(f"{operation_id}_migration", authorization_target["profileId"], authorization_target["database"], authorization_target["namespace"], saved["schema"], False, binding)
            apply_action = {"type": "migration_apply", "profileId": authorization_target["profileId"], "database": authorization_target["database"], "namespace": authorization_target["namespace"], "planId": plan["applyPlanId"], "destructive": plan["destructive"], "reviewDigest": plan["reviewDigest"], "requiresConfirmation": True}
            chat = self.authority.get_chat(session_id)
            proposal = self.authority.create_proposal(session_id, apply_action, self.policy_binding(chat, apply_action, origin="server_apply"), authorization_target, {"revision": receipt["revision"], "layoutToken": receipt["layoutToken"]})
            receipt["migrationPreview"] = {"status": "ready", "kind": "migration_plan", "plan": plan, "target": authorization_target, "schemaBinding": binding, "applyProposal": proposal_envelope(proposal, session_id, chat)}
        except (PostgresServiceError, MetadataStoreError) as error:
            payload = error.payload if hasattr(error, "payload") else error.to_dict()
            receipt["migrationPreview"] = {"status": "unavailable", "error": payload["error"]}
        return receipt

import copy
import uuid

from schemii.metadata import MetadataStoreError


class FakeSchemiiAuthority:
    """Injectable authority double for HTTP tests, never used by production."""

    permissions = ("schema", "structured", "write", "rawread", "rawwrite")

    def __init__(self):
        self.chats = {}
        self.proposals = {}
        self.operations = {}
        self.results = {}
        self.put_chat("ses_1", "schema_one", "ses_1")

    def put_chat(self, chat_id, schema_id, external_id, target=None, capabilities=None, approvals=None):
        self.chats[chat_id] = {
            "id": chat_id, "schemaId": schema_id, "externalSessionId": external_id,
            "title": "Schema chat", "target": target or {}, "capabilities": capabilities or ["schema"],
            "approvals": approvals or {name: "every_action" for name in self.permissions},
            "policyRevision": 1, "grants": {}, "state": "active",
        }
        return copy.deepcopy(self.chats[chat_id])

    def configure(self, chat_id, payload, service):
        if chat_id not in self.chats:
            self.put_chat(chat_id, payload.get("schemaId", "schema_one"), "ses_1")
        chat = self.chats[chat_id]
        access = payload.get("accessLevel")
        if access:
            chat["capabilities"] = [name for name in self.permissions if name in access.split("-")]
            if access in {"data", "schema-data", "schema-read-write"}:
                chat["capabilities"] = ["rawread", "write"] + (["schema"] if access != "data" else [])
        if payload.get("profileId"):
            chat["target"] = {
                "profileId": payload["profileId"], "database": payload["database"],
                "namespace": payload["namespace"],
                "profileFingerprint": service.profile_context_fingerprint(payload["profileId"]),
            }
        chat["schemaId"] = payload.get("schemaId", chat["schemaId"])

    def health(self): return {"ok": True, "version": 4, "expectedVersion": 4}

    def provision_chat(self, schema_id):
        chat_id = str(uuid.uuid4())
        self.chats[chat_id] = {"id": chat_id, "schemaId": schema_id, "state": "provisioning"}
        return {"chatId": chat_id}

    def bind_external_session(self, chat_id, external_id, title):
        self.chats[chat_id].update({"externalSessionId": external_id, "title": title})
        return copy.deepcopy(self.chats[chat_id])

    def activate_chat(self, chat_id, target, capabilities, approvals):
        current = self.chats[chat_id]
        return self.put_chat(chat_id, current["schemaId"], current["externalSessionId"], target, capabilities, approvals)

    def fail_chat(self, chat_id, reason): self.chats[chat_id]["state"] = "failed"; return copy.deepcopy(self.chats[chat_id])

    def get_chat(self, chat_id):
        chat = self.chats.get(chat_id)
        if not chat or chat.get("state") != "active":
            raise MetadataStoreError("chat_not_found", "AI chat was not found", status=404)
        return copy.deepcopy(chat)

    def list_chats(self, schema_id, target=None):
        return [self.get_chat(key) for key, value in self.chats.items() if value.get("state") == "active" and value["schemaId"] == schema_id and (target is None or value["target"] == target)]

    def update_policy(self, chat_id, capabilities, approvals, expected_revision):
        chat = self.chats[chat_id]
        if chat["policyRevision"] != expected_revision:
            raise MetadataStoreError("chat_policy_changed", "AI chat policy changed", status=409)
        chat.update({"capabilities": capabilities, "approvals": approvals, "policyRevision": expected_revision + 1})
        return self.get_chat(chat_id)

    def begin_delete(self, chat_id): self.chats[chat_id]["state"] = "deleting"; return copy.deepcopy(self.chats[chat_id])
    def finish_delete(self, chat_id): self.chats[chat_id]["state"] = "deleted"; return {"state": "deleted"}

    def create_proposal(self, chat_id, action, policy, target, concurrency):
        proposal_id = str(uuid.uuid4())
        record = {"id": proposal_id, "chatId": chat_id, "state": "ready", "action": copy.deepcopy(action), "policyBinding": copy.deepcopy(policy), "authorizationTarget": copy.deepcopy(target), "schemaConcurrency": copy.deepcopy(concurrency)}
        self.proposals[proposal_id] = record
        return copy.deepcopy(record)

    def proposal(self, proposal_id, chat_id):
        record = self.proposals.get(proposal_id)
        if record is None:
            raise MetadataStoreError("proposal_not_found", "Proposal was not found", status=404)
        if record["chatId"] != chat_id:
            raise MetadataStoreError("authority_binding_mismatch", "Proposal belongs to another chat", status=403)
        return copy.deepcopy(record)

    def pending_proposals(self, chat_id):
        return [self.proposal(key, chat_id) for key, value in self.proposals.items() if value["chatId"] == chat_id and value["state"] in {"ready", "authorized"}]

    def authorize_and_claim(self, proposal_id, chat_id, revision, confirmation):
        existing = self.operation_for_proposal(proposal_id, chat_id)
        if existing:
            return {**existing, "executionOwner": False}, {"policyRevision": revision}
        proposal = self.proposal(proposal_id, chat_id)
        operation_id = str(uuid.uuid4())
        attempt_id = str(uuid.uuid4())
        self.operations[operation_id] = {"id": operation_id, "proposalId": proposal_id, "chatId": chat_id, "state": "running", "result": None, "error": None, "attemptId": attempt_id}
        self.proposals[proposal_id]["state"] = "authorized"
        public = copy.deepcopy(self.operations[operation_id])
        public.update({"executionOwner": True, "claimToken": attempt_id})
        return public, {"policyRevision": revision}

    def operation(self, operation_id, chat_id):
        operation = self.operations.get(operation_id)
        if operation is None or operation["chatId"] != chat_id:
            raise MetadataStoreError("operation_not_found", "Operation was not found", status=404)
        result = copy.deepcopy(operation)
        result.pop("attemptId", None)
        return result

    def operation_for_proposal(self, proposal_id, chat_id):
        return next((self.operation(key, chat_id) for key, value in self.operations.items() if value["proposalId"] == proposal_id), None)

    def finish_operation(self, attempt_id, token, state, result=None, error=None):
        operation = next(value for value in self.operations.values() if value.get("attemptId") == attempt_id)
        operation.update({"state": state, "result": copy.deepcopy(result), "error": copy.deepcopy(error)})
        return self.operation(operation["id"], operation["chatId"])

    def resolve_operation(self, operation_id, chat_id, state, result=None, error=None):
        self.operations[operation_id].update({"state": state, "result": copy.deepcopy(result), "error": copy.deepcopy(error)})
        return self.operation(operation_id, chat_id)

    def create_result(self, chat_id, binding, payload):
        result_id = str(uuid.uuid4())
        self.results[result_id] = {"chatId": chat_id, "binding": copy.deepcopy(binding), "payload": copy.deepcopy(payload)}
        return {"id": result_id}

    def reserve_result(self, result_id, chat_id, binding): return {"deliveryId": str(uuid.uuid4()), "reservationToken": result_id, "payload": copy.deepcopy(self.results[result_id]["payload"])}
    def begin_result_delivery(self, delivery_id, token): return {"state": "delivering"}
    def consume_result(self, delivery_id, token): return {"state": "consumed"}
    def release_result(self, delivery_id, token): return {"state": "released"}
    def uncertain_result(self, delivery_id, token): return {"state": "uncertain"}

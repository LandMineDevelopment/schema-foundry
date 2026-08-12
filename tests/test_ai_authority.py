import sys
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.ai_authority import AiAuthority, AiAuthorityError


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class AiAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.authority = AiAuthority(
            max_entries=10, proposal_ttl=20, claim_lease=5,
            result_ttl=10, max_payload_bytes=2048, clock=self.clock,
        )
        self.proposal_context = {
            "application": "schemii", "session_id": "session-1",
            "resource": "schema-1", "access": "write",
            "binding": {"revision": 3},
        }
        self.result_context = {
            "application": "schemer", "session_id": "session-1",
            "resource": "dashboard-1", "target": {"database": "demo"},
            "binding": {"revision": 7},
        }

    def register_proposal(self, action=None):
        return self.authority.register_proposal(
            **self.proposal_context, action=action or {"type": "add_table", "table": {"name": "orders"}},
        )

    def register_result(self, result=None):
        return self.authority.register_query_result(
            **self.result_context, result=result or {"columns": ["count"], "rows": [[3]]},
        )

    def assert_error(self, code, call):
        with self.assertRaises(AiAuthorityError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.payload["error"]["code"], code)
        return caught.exception

    def test_register_list_claim_finalize_and_no_replay(self):
        envelope = self.register_proposal()
        self.assertEqual(self.authority.list_proposals(**self.proposal_context), [envelope])
        claim = self.authority.claim_proposal(envelope["id"], **self.proposal_context)
        self.assertEqual(claim["action"]["type"], "add_table")
        self.assertEqual(claim["proposal"]["state"], "claimed")
        consumed = self.authority.finalize_proposal(
            envelope["id"], claim["claimToken"], application="schemii", session_id="session-1",
        )
        self.assertEqual(consumed["state"], "consumed")
        self.assert_error(
            "proposal_consumed",
            lambda: self.authority.claim_proposal(envelope["id"], **self.proposal_context),
        )
        self.assert_error(
            "invalid_claim",
            lambda: self.authority.finalize_proposal(
                envelope["id"], claim["claimToken"], application="schemii", session_id="session-1",
            ),
        )

    def test_proposal_expiry_and_expired_claim_lease_recovery(self):
        envelope = self.register_proposal()
        first = self.authority.claim_proposal(envelope["id"], **self.proposal_context)
        self.clock.advance(5)
        second = self.authority.claim_proposal(envelope["id"], **self.proposal_context)
        self.assertNotEqual(first["claimToken"], second["claimToken"])
        self.assert_error(
            "invalid_claim",
            lambda: self.authority.release_proposal(
                envelope["id"], first["claimToken"], application="schemii", session_id="session-1",
            ),
        )
        self.authority.release_proposal(
            envelope["id"], second["claimToken"], application="schemii", session_id="session-1",
        )
        self.clock.advance(15)
        self.assert_error(
            "proposal_not_found",
            lambda: self.authority.claim_proposal(envelope["id"], **self.proposal_context),
        )

    def test_claim_is_atomic_across_threads(self):
        envelope = self.register_proposal()
        barrier = threading.Barrier(12)
        claims = []
        errors = []
        guard = threading.Lock()

        def claim():
            barrier.wait()
            try:
                value = self.authority.claim_proposal(envelope["id"], **self.proposal_context)
                with guard:
                    claims.append(value)
            except AiAuthorityError as error:
                with guard:
                    errors.append(error.code)

        threads = [threading.Thread(target=claim) for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(claims), 1)
        self.assertEqual(errors, ["proposal_claimed"] * 11)

    def test_proposal_rejects_each_wrong_authority_binding(self):
        envelope = self.register_proposal()
        for key, value in (
            ("application", "schemer"), ("session_id", "other"),
            ("resource", "other"), ("access", "read"), ("binding", {"revision": 4}),
        ):
            context = {**self.proposal_context, key: value}
            self.assert_error(
                "proposal_binding_mismatch",
                lambda context=context: self.authority.claim_proposal(envelope["id"], **context),
            )

    def test_wrong_claim_token_cannot_finalize_or_release(self):
        envelope = self.register_proposal()
        claim = self.authority.claim_proposal(envelope["id"], **self.proposal_context)
        self.assert_error(
            "invalid_claim",
            lambda: self.authority.finalize_proposal(
                envelope["id"], "wrong", application="schemii", session_id="session-1",
            ),
        )
        self.assert_error(
            "invalid_claim",
            lambda: self.authority.release_proposal(
                envelope["id"], "wrong", application="schemii", session_id="session-1",
            ),
        )
        self.assert_error(
            "proposal_binding_mismatch",
            lambda: self.authority.finalize_proposal(
                envelope["id"], claim["claimToken"], application="schemer", session_id="session-1",
            ),
        )
        self.authority.finalize_proposal(
            envelope["id"], claim["claimToken"], application="schemii", session_id="session-1",
        )

    def test_proposal_payload_and_binding_are_owned_copies(self):
        action = {"type": "change", "items": [{"name": "before"}]}
        binding = {"revision": {"value": 1}}
        context = {**self.proposal_context, "binding": binding}
        envelope = self.authority.register_proposal(**context, action=action)
        action["items"][0]["name"] = "outside"
        binding["revision"]["value"] = 2
        envelope["action"]["items"][0]["name"] = "public"
        envelope["binding"]["revision"]["value"] = 3
        expected = {**self.proposal_context, "binding": {"revision": {"value": 1}}}
        claim = self.authority.claim_proposal(envelope["id"], **expected)
        self.assertEqual(claim["action"]["items"][0]["name"], "before")
        claim["action"]["items"][0]["name"] = "returned"
        self.authority.release_proposal(
            envelope["id"], claim["claimToken"], application="schemii", session_id="session-1",
        )
        again = self.authority.claim_proposal(envelope["id"], **expected)
        self.assertEqual(again["action"]["items"][0]["name"], "before")

    def test_capacity_is_shared_and_expired_entries_free_space(self):
        authority = AiAuthority(max_entries=2, proposal_ttl=2, result_ttl=2, clock=self.clock)
        authority.register_proposal(**self.proposal_context, action={"type": "one"})
        authority.register_query_result(**self.result_context, result={"rows": []})
        self.assert_error(
            "authority_capacity",
            lambda: authority.register_proposal(**self.proposal_context, action={"type": "three"}),
        )
        self.clock.advance(2)
        authority.register_proposal(**self.proposal_context, action={"type": "three"})

    def test_query_result_reserve_release_consume_is_one_use(self):
        reference = self.register_result()
        first = self.authority.reserve_query_result(reference["id"], **self.result_context)
        self.assertEqual(first["result"]["rows"], [[3]])
        self.authority.release_query_result(
            reference["id"], first["reservationToken"], application="schemer", session_id="session-1",
        )
        second = self.authority.reserve_query_result(reference["id"], **self.result_context)
        self.assertNotEqual(first["reservationToken"], second["reservationToken"])
        consumed = self.authority.consume_query_result(
            reference["id"], second["reservationToken"], application="schemer", session_id="session-1",
        )
        self.assertEqual(consumed["state"], "consumed")
        self.assert_error(
            "result_consumed",
            lambda: self.authority.reserve_query_result(reference["id"], **self.result_context),
        )
        self.assert_error(
            "invalid_result_reservation",
            lambda: self.authority.consume_query_result(
                reference["id"], second["reservationToken"], application="schemer", session_id="session-1",
            ),
        )

    def test_query_result_rejects_cross_binding_and_wrong_token(self):
        reference = self.register_result()
        for key, value in (
            ("application", "schemii"), ("session_id", "other"),
            ("resource", "other"), ("target", {"database": "other"}),
            ("binding", {"revision": 8}),
        ):
            context = {**self.result_context, key: value}
            self.assert_error(
                "result_binding_mismatch",
                lambda context=context: self.authority.reserve_query_result(reference["id"], **context),
            )
        reservation = self.authority.reserve_query_result(reference["id"], **self.result_context)
        self.assert_error(
            "invalid_result_reservation",
            lambda: self.authority.release_query_result(
                reference["id"], "wrong", application="schemer", session_id="session-1",
            ),
        )
        self.assert_error(
            "result_binding_mismatch",
            lambda: self.authority.release_query_result(
                reference["id"], reservation["reservationToken"], application="schemii", session_id="session-1",
            ),
        )
        self.authority.release_query_result(
            reference["id"], reservation["reservationToken"], application="schemer", session_id="session-1",
        )

    def test_query_result_reservation_is_atomic_across_threads(self):
        reference = self.register_result()
        barrier = threading.Barrier(8)
        reservations = []
        errors = []
        guard = threading.Lock()

        def reserve():
            barrier.wait()
            try:
                value = self.authority.reserve_query_result(reference["id"], **self.result_context)
                with guard:
                    reservations.append(value)
            except AiAuthorityError as error:
                with guard:
                    errors.append(error.code)

        threads = [threading.Thread(target=reserve) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(reservations), 1)
        self.assertEqual(errors, ["result_reserved"] * 7)

    def test_query_result_expiry_and_copy_ownership(self):
        result = {"rows": [[{"value": 1}]]}
        target = {"database": {"name": "demo"}}
        binding = {"revision": [7]}
        context = {**self.result_context, "target": target, "binding": binding}
        reference = self.authority.register_query_result(**context, result=result)
        result["rows"][0][0]["value"] = 2
        target["database"]["name"] = "other"
        binding["revision"][0] = 8
        reference["target"]["database"]["name"] = "returned"
        expected = {
            **self.result_context,
            "target": {"database": {"name": "demo"}},
            "binding": {"revision": [7]},
        }
        reserved = self.authority.reserve_query_result(reference["id"], **expected)
        self.assertEqual(reserved["result"]["rows"][0][0]["value"], 1)
        self.clock.advance(10)
        self.assert_error(
            "result_not_found",
            lambda: self.authority.release_query_result(
                reference["id"], reserved["reservationToken"], application="schemer", session_id="session-1",
            ),
        )

    def test_validation_and_payload_bounds(self):
        self.assert_error(
            "invalid_authority_input",
            lambda: self.authority.register_proposal(**self.proposal_context, action={"value": float("nan")}),
        )
        self.assert_error(
            "authority_payload_too_large",
            lambda: self.authority.register_query_result(**self.result_context, result={"value": "x" * 3000}),
        )
        self.assert_error(
            "invalid_authority_input",
            lambda: self.authority.register_proposal(
                **{**self.proposal_context, "application": " schemii"}, action={"type": "x"},
            ),
        )


if __name__ == "__main__":
    unittest.main()

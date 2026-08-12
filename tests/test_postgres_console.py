import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.postgres_common import PostgresServiceError, ValidationError
from schemii.postgres_console import ConsoleExecutionRegistry, ConsolePolicy, split_console_statements
from schemii.postgres_service import PostgresService


PROFILE = {
    "name": "Local", "host": "localhost", "port": 5432, "dbname": "demo",
    "user": "developer", "password": "secret", "sslmode": "prefer", "timeout": 5,
}


class Column:
    def __init__(self, name):
        self.name = name


class Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.description = None
        self.rows = []
        self.statusmessage = ""
        self.rowcount = -1

    def execute(self, sql, params=()):
        self.connection.executed.append((sql, params))
        if self.connection.block_on == sql:
            self.connection.started.set()
            self.connection.release.wait(2)
            if self.connection.cancelled:
                raise CancelledError()
        if sql == "SELECT current_database() AS database":
            self.rows = [{"database": self.connection.database}]
            self.description = [Column("database")]
            self.statusmessage = "SELECT 1"
        elif "SELECT EXISTS" in sql:
            self.rows = [{"exists": self.connection.namespace_exists}]
            self.description = [Column("exists")]
            self.statusmessage = "SELECT 1"
        elif sql in self.connection.results:
            columns, self.rows, self.statusmessage, self.rowcount = self.connection.results[sql]
            self.description = [Column(name) for name in columns] if columns is not None else None
            for notice in self.connection.notices.get(sql, []):
                for handler in self.connection.notice_handlers:
                    handler(type("Notice", (), {"message_primary": notice})())
        else:
            self.rows = []
            self.description = None
            self.statusmessage = "SET"
            self.rowcount = -1

    def fetchall(self):
        return self.rows

    def fetchmany(self, size):
        return self.rows[:size]

    def close(self):
        pass


class Diagnostic:
    message_primary = "canceling statement due to user request"


class CancelledError(Exception):
    sqlstate = "57014"
    diag = Diagnostic()


class UnsupportedTransactionError(Exception):
    sqlstate = "25001"
    diag = None


class Connection:
    def __init__(self, results=None, *, database="demo", namespace_exists=True, block_on=None, notices=None, commit_error=None):
        self.results = results or {}
        self.database = database
        self.namespace_exists = namespace_exists
        self.block_on = block_on
        self.notices = notices or {}
        self.notice_handlers = []
        self.executed = []
        self.rollbacks = 0
        self.commits = 0
        self.commit_error = commit_error
        self.closed = False
        self.cancelled = False
        self.started = threading.Event()
        self.release = threading.Event()

    def cursor(self):
        return Cursor(self)

    def cancel(self):
        self.cancelled = True
        self.release.set()

    def add_notice_handler(self, handler):
        self.notice_handlers.append(handler)

    def remove_notice_handler(self, handler):
        self.notice_handlers.remove(handler)

    def rollback(self):
        self.rollbacks += 1

    def commit(self):
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error

    def close(self):
        self.closed = True


class PostgresConsoleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.connection = Connection()
        self.service = PostgresService(self.directory.name, connect_factory=lambda **kwargs: self.connection)
        self.service.save_profile("local", PROFILE)

    def tearDown(self):
        self.service.close()
        self.directory.cleanup()

    def request(self, sql, **changes):
        request = {
            "executionId": str(uuid4()), "consoleId": str(uuid4()), "database": "demo",
            "namespace": "public", "sql": sql, "mode": "read", "writeGrantId": None,
        }
        request.update(changes)
        return request

    def grant_request(self, console_id=None, **changes):
        request = {
            "consoleId": console_id or str(uuid4()), "database": "demo",
            "namespace": "public", "confirmed": True,
        }
        request.update(changes)
        return request

    def create_grant(self, console_id=None, binding="binding", server_id="server"):
        request = self.grant_request(console_id)
        result = self.service.create_console_write_grant("local", request, binding, server_id)
        return request, result["writeGrantId"]

    def test_scanner_splits_quotes_comments_and_dollar_quotes(self):
        statements = split_console_statements("SELECT ';'; -- ;\n SELECT $$;$$; /* ; */ SELECT 3")
        self.assertEqual(statements, ["SELECT ';'", "-- ;\n SELECT $$;$$", "/* ; */ SELECT 3"])
        with self.assertRaises(ValidationError):
            split_console_statements("SELECT 'unterminated")
        for sql in ("BEGIN; SELECT 1", "SET TRANSACTION READ WRITE", "SET LOCAL TRANSACTION READ ONLY", "ROLLBACK TO SAVEPOINT x"):
            with self.subTest(sql=sql), self.assertRaises(PostgresServiceError) as error:
                split_console_statements(sql)
            self.assertEqual(error.exception.code, "unsupported_transaction_control")
        with self.assertRaises(PostgresServiceError) as error:
            split_console_statements(";".join("SELECT 1" for _ in range(21)))
        self.assertEqual(error.exception.code, "too_many_statements")

    def test_executes_ordered_results_in_one_read_transaction_and_rolls_back(self):
        self.connection.results = {
            "SELECT 1 AS value": (["value"], [(1,)], "SELECT 1", 1),
            "UPDATE example SET value = value": (None, [], "UPDATE 2", 2),
        }
        result = self.service.execute_console(
            "local", self.request("SELECT 1 AS value; UPDATE example SET value = value"), "binding", "server",
        )
        self.assertEqual([entry["command"] for entry in result["statements"]], ["SELECT", "UPDATE"])
        self.assertEqual(result["statements"][0]["rows"], [[1]])
        self.assertEqual(result["statements"][1]["rowCount"], 2)
        self.assertFalse(result["committed"])
        self.assertEqual(self.connection.executed[0][0], "SET TRANSACTION READ ONLY")
        self.assertIn(("SELECT pg_catalog.set_config('search_path', %s, true)", ('"public"',)), self.connection.executed)
        self.assertEqual(self.connection.rollbacks, 1)
        self.assertTrue(self.connection.closed)

    def test_verifies_target_before_user_sql(self):
        self.connection.database = "other"
        with self.assertRaises(PostgresServiceError) as error:
            self.service.execute_console("local", self.request("SELECT secret"), "binding", "server")
        self.assertEqual(error.exception.code, "database_changed")
        self.assertFalse(any(sql == "SELECT secret" for sql, _ in self.connection.executed))

    def test_write_grant_creation_verifies_exact_request_and_live_target(self):
        request, grant_id = self.create_grant()
        self.assertEqual(str(uuid4()).count("-"), grant_id.count("-"))
        self.assertIn(("SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = %s) AS exists", ("public",)), self.connection.executed)
        self.assertEqual(self.connection.rollbacks, 1)
        self.assertEqual(self.connection.commits, 0)
        self.assertNotIn("password", self.service._console.write_grants._entries[grant_id])
        with self.assertRaises(ValidationError):
            self.service.create_console_write_grant("local", {**request, "confirmed": False}, "binding", "server")

        self.connection.database = "other"
        with self.assertRaises(PostgresServiceError) as error:
            self.service.create_console_write_grant("local", self.grant_request(), "binding", "server")
        self.assertEqual(error.exception.code, "database_changed")

    def test_write_grant_requires_exact_binding_and_target(self):
        console_id = str(uuid4())
        _, grant_id = self.create_grant(console_id)
        for changes in (
            {"consoleId": str(uuid4())}, {"namespace": "other"},
        ):
            request = self.request(
                "UPDATE example SET value = 1", consoleId=console_id, mode="write", writeGrantId=grant_id,
            )
            request.update(changes)
            with self.subTest(changes=changes), self.assertRaises(PostgresServiceError) as error:
                self.service.execute_console(
                    "local", request,
                    "binding", "server", ConsolePolicy(allow_write=True),
                )
            self.assertEqual(error.exception.code, "write_grant_target_changed")
        with self.assertRaises(PostgresServiceError) as error:
            self.service.execute_console(
                "local", self.request("UPDATE example SET value = 1", consoleId=console_id, mode="write", writeGrantId=grant_id),
                "wrong-binding", "server", ConsolePolicy(allow_write=True),
            )
        self.assertEqual(error.exception.code, "write_grant_required")
        with self.assertRaises(PostgresServiceError) as error:
            self.service.revoke_console_write_grant("local", grant_id, "wrong-binding", "server")
        self.assertEqual((error.exception.status, error.exception.code), (404, "write_grant_not_found"))

    def test_write_grant_expiry_and_profile_fingerprint(self):
        now = [1000.0]
        service = PostgresService(self.directory.name, connect_factory=lambda **kwargs: self.connection, clock=lambda: now[0])
        console_id = str(uuid4())
        grant_id = service.create_console_write_grant("local", self.grant_request(console_id), "binding", "server")["writeGrantId"]
        now[0] += 300
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_console(
                "local", self.request("UPDATE x SET y = 1", consoleId=console_id, mode="write", writeGrantId=grant_id),
                "binding", "server", ConsolePolicy(allow_write=True),
            )
        self.assertEqual(error.exception.code, "write_grant_expired")

        now[0] = 2000
        grant_id = service.create_console_write_grant("local", self.grant_request(console_id), "binding", "server")["writeGrantId"]
        service.save_profile("local", {**PROFILE, "user": "changed"})
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_console(
                "local", self.request("UPDATE x SET y = 1", consoleId=console_id, mode="write", writeGrantId=grant_id),
                "binding", "server", ConsolePolicy(allow_write=True),
            )
        self.assertEqual(error.exception.code, "write_grant_target_changed")
        service.close()

    def test_write_grant_absolute_expiry_is_not_extended_by_commits(self):
        now = [1000.0]
        service = PostgresService(self.directory.name, connect_factory=lambda **kwargs: self.connection, clock=lambda: now[0])
        console_id = str(uuid4())
        grant_id = service.create_console_write_grant("local", self.grant_request(console_id), "binding", "server")["writeGrantId"]
        for elapsed in (299, 598, 897):
            now[0] = 1000 + elapsed
            service.execute_console(
                "local", self.request("UPDATE x SET y = 1", consoleId=console_id, mode="write", writeGrantId=grant_id),
                "binding", "server", ConsolePolicy(allow_write=True),
            )
        now[0] = 1900
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_console(
                "local", self.request("UPDATE x SET y = 2", consoleId=console_id, mode="write", writeGrantId=grant_id),
                "binding", "server", ConsolePolicy(allow_write=True),
            )
        self.assertEqual(error.exception.code, "write_grant_expired")
        service.close()

    def test_write_commits_once_without_read_only_and_touches_grant(self):
        now = [1000.0]
        service = PostgresService(self.directory.name, connect_factory=lambda **kwargs: self.connection, clock=lambda: now[0])
        console_id = str(uuid4())
        grant_id = service.create_console_write_grant("local", self.grant_request(console_id), "binding", "server")["writeGrantId"]
        self.connection.executed.clear()
        self.connection.rollbacks = 0
        now[0] = 1299
        result = service.execute_console(
            "local", self.request("UPDATE example SET value = 1", consoleId=console_id, mode="write", writeGrantId=grant_id),
            "binding", "server", ConsolePolicy(allow_write=True),
        )
        self.assertTrue(result["committed"])
        self.assertEqual(result["mode"], "write")
        self.assertEqual((self.connection.commits, self.connection.rollbacks), (1, 0))
        self.assertNotIn("SET TRANSACTION READ ONLY", [sql for sql, _ in self.connection.executed])
        now[0] = 1598
        service.execute_console(
            "local", self.request("UPDATE example SET value = 2", consoleId=console_id, mode="write", writeGrantId=grant_id),
            "binding", "server", ConsolePolicy(allow_write=True),
        )
        self.assertEqual(self.connection.commits, 2)
        service.close()

    def test_failed_write_rolls_back_and_does_not_extend_idle(self):
        now = [1000.0]
        failure = RuntimeError("commit failed")
        connection = Connection(commit_error=failure)
        service = PostgresService(self.directory.name, connect_factory=lambda **kwargs: connection, clock=lambda: now[0])
        console_id = str(uuid4())
        grant_id = service.create_console_write_grant("local", self.grant_request(console_id), "binding", "server")["writeGrantId"]
        connection.rollbacks = 0
        now[0] = 1200
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_console(
                "local", self.request("UPDATE example SET value = 1", consoleId=console_id, mode="write", writeGrantId=grant_id),
                "binding", "server", ConsolePolicy(allow_write=True),
            )
        self.assertEqual(error.exception.code, "sql_query_failed")
        self.assertEqual((connection.commits, connection.rollbacks), (1, 1))
        connection.commit_error = None
        now[0] = 1300
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_console(
                "local", self.request("UPDATE example SET value = 2", consoleId=console_id, mode="write", writeGrantId=grant_id),
                "binding", "server", ConsolePolicy(allow_write=True),
            )
        self.assertEqual(error.exception.code, "write_grant_expired")
        service.close()

    def test_limits_rows_columns_and_aggregate_bytes(self):
        self.connection.results = {"SELECT rows": (["value"], [(index,) for index in range(501)], "SELECT 501", 501)}
        result = self.service.execute_console("local", self.request("SELECT rows"), "binding", "server")
        self.assertEqual(result["statements"][0]["rowCount"], 500)
        self.assertTrue(result["statements"][0]["truncated"])
        self.assertLessEqual(len(json.dumps(result, separators=(",", ":")).encode()), 1024 * 1024)

        connection = Connection({"SELECT wide": ([f"c{i}" for i in range(101)], [], "SELECT 0", 0)})
        service = PostgresService(self.directory.name, connect_factory=lambda **kwargs: connection)
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_console("local", self.request("SELECT wide"), "binding", "server")
        self.assertEqual(error.exception.code, "sql_result_too_wide")

    def test_collects_and_bounds_notices_across_statements(self):
        self.connection.results = {
            "SELECT first": (["value"], [(1,)], "SELECT 1", 1),
            "SELECT second": (["value"], [(2,)], "SELECT 1", 1),
        }
        self.connection.notices = {
            "SELECT first": [f"notice {index}" for index in range(49)],
            "SELECT second": ["last notice", "discarded notice"],
        }
        result = self.service.execute_console(
            "local", self.request("SELECT first; SELECT second"), "binding", "server",
        )
        self.assertEqual(len(result["statements"][0]["notices"]), 49)
        self.assertEqual(result["statements"][1]["notices"], ["last notice"])
        self.assertEqual(self.connection.notice_handlers, [])

    def test_registry_enforces_process_and_console_concurrency_and_visibility(self):
        registry = ConsoleExecutionRegistry(maximum_active=2)
        first, second, third = str(uuid4()), str(uuid4()), str(uuid4())
        registry.reserve(first, "console-a", "one", "binding-a", "server")
        with self.assertRaises(PostgresServiceError) as error:
            registry.reserve(first, "console-z", "one", "binding-a", "server")
        self.assertEqual(error.exception.code, "execution_conflict")
        with self.assertRaises(PostgresServiceError) as error:
            registry.reserve(second, "console-a", "two", "binding-b", "server")
        self.assertEqual(error.exception.code, "execution_busy")
        registry.reserve(second, "console-b", "one", "binding-a", "server")
        with self.assertRaises(PostgresServiceError) as error:
            registry.reserve(third, "console-c", "one", "binding-a", "server")
        self.assertEqual(error.exception.code, "execution_busy")
        with self.assertRaises(PostgresServiceError) as error:
            registry.cancel(first, "wrong", "binding-a", "server")
        self.assertEqual(error.exception.code, "execution_not_found")

        pending = ConsoleExecutionRegistry()
        pending_id = str(uuid4())
        pending.reserve(pending_id, "console-pending", "one", "binding", "server")
        self.assertEqual(pending.cancel(pending_id, "one", "binding", "server"), {"requested": True})
        connection = Connection()
        self.assertTrue(pending.attach(pending_id, connection))
        self.assertTrue(connection.cancelled)

    def test_cancel_during_statement_is_distinguished_from_timeout(self):
        self.connection.block_on = "SELECT slow"
        request = self.request("SELECT slow")
        outcome = []

        def execute():
            try:
                self.service.execute_console("local", request, "binding", "server")
            except PostgresServiceError as error:
                outcome.append(error)

        thread = threading.Thread(target=execute)
        thread.start()
        self.assertTrue(self.connection.started.wait(1))
        self.assertEqual(self.service.cancel_console("local", request["executionId"], "binding", "server"), {"requested": True})
        thread.join(2)
        self.assertEqual(outcome[0].code, "execution_cancelled")
        self.assertEqual(outcome[0].details, {"statementIndex": 0})
        self.assertEqual(self.connection.rollbacks, 1)
        self.assertTrue(self.connection.closed)

    def test_maps_commands_unsupported_in_transaction(self):
        error = self.service._console._error(UnsupportedTransactionError(), 2, False)
        self.assertEqual(error.code, "unsupported_in_transaction")
        self.assertEqual(error.details, {"statementIndex": 2, "sqlstate": "25001"})


if __name__ == "__main__":
    unittest.main()

import sys
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.postgres_common import PostgresServiceError
from schemii.postgres_concurrency import PostgresExecutionController


class PostgresExecutionControllerTests(unittest.TestCase):
    def test_class_and_global_backpressure_are_structured_and_release(self):
        controller = PostgresExecutionController(
            {"catalog": 1, "read": 1, "console": 1, "write": 1}, global_capacity=1,
        )
        entered = threading.Event()
        release = threading.Event()

        def hold():
            with controller.execution("read"):
                entered.set()
                release.wait(2)

        thread = threading.Thread(target=hold)
        thread.start()
        self.assertTrue(entered.wait(1))
        with self.assertRaises(PostgresServiceError) as caught:
            with controller.execution("catalog"):
                pass
        self.assertEqual((caught.exception.status, caught.exception.code), (429, "postgres_execution_busy"))
        self.assertTrue(caught.exception.details["retryable"])
        release.set()
        thread.join(2)
        with controller.execution("catalog"):
            pass
        snapshot = controller.snapshot()
        self.assertEqual(snapshot["global"]["active"], 0)
        self.assertEqual(snapshot["classes"]["catalog"]["rejected"], 1)
        self.assertEqual(snapshot["classes"]["read"]["completed"], 1)

    def test_failure_and_close_are_observable(self):
        controller = PostgresExecutionController()
        with self.assertRaisesRegex(RuntimeError, "boom"):
            with controller.execution("write"):
                raise RuntimeError("boom")
        self.assertEqual(controller.snapshot()["classes"]["write"]["failed"], 1)
        controller.close()
        with self.assertRaises(PostgresServiceError) as caught:
            with controller.execution("read"):
                pass
        self.assertEqual((caught.exception.status, caught.exception.code), (503, "postgres_execution_unavailable"))


if __name__ == "__main__":
    unittest.main()

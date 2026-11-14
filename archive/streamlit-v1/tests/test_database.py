"""Operational metrics must never mix simulated events with real events."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import unittest

from coconutray.database import Database


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "metrics.sqlite3"
        self.db = Database(self.path)

    def tearDown(self):
        self.db.close()
        self.directory.cleanup()

    def test_empty_metrics_and_mode_validation(self):
        self.assertEqual(self.db.metrics("demo"), {
            "conversations": 0, "average_seconds": 0, "corrections": 0,
            "matching_errors": 0, "carts": 0, "checkouts": 0, "integration_errors": 0,
        })
        with self.assertRaises(ValueError):
            self.db.start_session("paid")

    def test_modes_stay_separate_and_failed_cart_is_not_generated_cart(self):
        demo = self.db.start_session("demo")
        real = self.db.start_session("real")
        analysis = self.db.record_analysis(demo, 2, 2, 1, matching_errors=1)
        self.db.record_corrections(analysis, 2)
        self.db.record_corrections(analysis, 2)
        self.db.record_analysis(real, 8, 1, 0)
        self.db.record_cart(demo, False, "demo")
        event = self.db.record_cart(demo, True, "demo", "sample-cart")
        self.db.record_checkout(event)
        self.db.record_checkout(event)
        self.db.record_error(real, "tray", "TimeoutError")
        self.assertEqual(self.db.metrics("demo"), {
            "conversations": 1, "average_seconds": 2, "corrections": 2,
            "matching_errors": 1, "carts": 1, "checkouts": 1, "integration_errors": 0,
        })
        self.assertEqual(self.db.metrics("real")["average_seconds"], 8)
        self.assertEqual(self.db.metrics("real")["integration_errors"], 1)
        self.assertEqual(self.db.metrics("real")["carts"], 0)
        with self.assertRaises(ValueError):
            self.db.record_cart(demo, True, "tray")
        with self.assertRaises(ValueError):
            self.db.record_cart(real, True, "demo")

    def test_checkout_requires_successful_cart(self):
        session = self.db.start_session("demo")
        failed = self.db.record_cart(session, False, "demo")
        with self.assertRaises(ValueError):
            self.db.record_checkout(failed)
        with self.assertRaises(ValueError):
            self.db.record_checkout(9876)
        self.assertEqual(self.db.metrics("demo")["checkouts"], 0)

    def test_cart_duplicate_is_idempotent_and_consumers_are_isolated(self):
        first = self.db.start_session("demo")
        second = self.db.start_session("demo")
        self.assertNotEqual(first, second)
        event = self.db.record_cart(first, True, "demo", "cart-id")
        self.assertEqual(event, self.db.record_cart(first, True, "demo", "cart-id"))
        self.db.record_cart(second, True, "demo", "cart-id")
        self.assertEqual(self.db.metrics("demo")["carts"], 2)

    def test_invalid_counters_and_unknown_session_never_enter_metrics(self):
        session = self.db.start_session("demo")
        for duration in (-1, float("nan"), float("inf"), True, "5"):
            with self.assertRaises(ValueError):
                self.db.record_analysis(session, duration, 1, 0)
        for invalid in (-1, 1.5, True):
            with self.assertRaises(ValueError):
                self.db.record_analysis(session, 1, invalid, 0)
        with self.assertRaises(ValueError):
            self.db.record_analysis("unknown", 1, 1, 0)
        with self.assertRaises(ValueError):
            self.db.record_corrections(999, 1)
        self.assertEqual(self.db.metrics("demo")["conversations"], 0)

    def test_restart_preserves_records_and_schema_foreign_keys(self):
        session = self.db.start_session("real")
        self.db.record_analysis(session, 3, 1, 1)
        self.db.close()
        self.db = Database(self.path)
        self.assertEqual(self.db.metrics("real")["conversations"], 1)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db._transaction() as connection:
                connection.execute(
                    "INSERT INTO integration_errors(session_id, service, error_type, created_at) VALUES (?, ?, ?, ?)",
                    ("unknown", "tray", "Error", "now"),
                )

    def test_no_personal_data_urls_or_plaintext_cart_ids_in_database(self):
        session = self.db.start_session("real")
        self.db.record_cart(session, True, "tray", "secret-consumer-cart-id")
        with self.assertRaises(ValueError):
            self.db.record_error(session, "tray", "Cliente Ana: erro em https://shop.example")
        connection = sqlite3.connect(self.path)
        try:
            dump = "\n".join(connection.iterdump())
        finally:
            connection.close()
        self.assertNotIn("secret-consumer-cart-id", dump)
        self.assertNotIn("https://", dump)
        self.assertNotIn("customer_name", dump)
        self.assertNotIn("conversation", dump)

    def test_recent_activity_is_filtered_and_includes_handoff_once(self):
        demo = self.db.start_session("demo")
        real = self.db.start_session("real")
        self.db.record_analysis(demo, 1, 1, 0)
        cart = self.db.record_cart(demo, True, "demo")
        self.db.record_checkout(cart)
        self.db.record_checkout(cart)
        self.db.record_error(real, "tray", "Unavailable")
        activity = self.db.recent_activity("demo")
        self.assertEqual({entry["event"] for entry in activity}, {"analysis", "cart", "checkout"})
        self.assertEqual(len(activity), 3)
        self.assertTrue(all(entry["session_id"] == demo for entry in activity))
        with self.assertRaises(ValueError):
            self.db.recent_activity("demo", 0)

    def test_concurrent_reruns_record_distinct_sessions_safely(self):
        def work(_):
            session = self.db.start_session("demo")
            self.db.record_analysis(session, 1.5, 1, 0)
            self.db.record_cart(session, True, "demo", "unique-per-consumer")
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(work, range(20)))
        self.assertEqual(self.db.metrics("demo")["conversations"], 20)
        self.assertEqual(self.db.metrics("demo")["carts"], 20)


if __name__ == "__main__":
    unittest.main()

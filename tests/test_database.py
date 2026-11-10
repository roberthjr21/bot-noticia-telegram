from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bot_noticias.database import Database
from bot_noticias.config import validate_time
from bot_noticias.news import DEFAULT_TOPICS


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.db = Database(Path(self.tempdir.name) / "test.sqlite3")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_subscribe_defaults_and_repeated_start(self):
        self.db.subscribe(123, 123, "08:00")
        self.assertEqual(self.db.topics(123), list(DEFAULT_TOPICS))
        self.db.toggle_topic(123, "ia")
        self.db.subscribe(123, 123, "08:00")
        self.assertNotIn("ia", self.db.topics(123))

    def test_toggle_never_removes_last_topic(self):
        self.db.subscribe(123, 123, "08:00")
        for topic in DEFAULT_TOPICS[:-1]:
            self.assertTrue(self.db.toggle_topic(123, topic)[0])
        self.assertFalse(self.db.toggle_topic(123, DEFAULT_TOPICS[-1])[0])

    def test_persist_time_pause_and_delivery(self):
        self.db.subscribe(123, 123, "08:00")
        self.db.set_time(123, "18:45")
        self.db.set_enabled(123, False)
        self.db.mark_delivered(123, ["hash123"])
        restored = Database(self.db.path)
        self.assertEqual(restored.get(123)["daily_time"], "18:45")
        self.assertEqual(restored.get(123)["enabled"], 0)
        self.assertIn("hash123", restored.delivered_fingerprints(123))

    def test_voice_preference_persists(self):
        self.db.subscribe(123, 123, "08:00")
        self.assertEqual(self.db.get(123)["voice_enabled"], 0)
        self.db.set_voice_enabled(123, True)
        restored = Database(self.db.path)
        self.assertEqual(restored.get(123)["voice_enabled"], 1)

    def test_migration_adds_voice_column_without_losing_data(self):
        import sqlite3
        legacy = Path(self.tempdir.name) / "legacy.sqlite3"
        with sqlite3.connect(legacy) as conn:
            conn.execute(
                "CREATE TABLE subscribers (chat_id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
                "daily_time TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, "
                "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            conn.execute(
                "INSERT INTO subscribers(chat_id, user_id, daily_time, enabled) VALUES (1, 2, '07:30', 1)"
            )
        migrated = Database(legacy)
        self.assertEqual(migrated.get(1)["daily_time"], "07:30")
        self.assertEqual(migrated.get(1)["voice_enabled"], 0)

    def test_validate_time(self):
        self.assertEqual(validate_time("08:00"), "08:00")
        for bad in ["24:00", "8:00", "12:60", "08:00:01", "foobar"]:
            with self.assertRaises(ValueError):
                validate_time(bad)


if __name__ == "__main__":
    unittest.main()

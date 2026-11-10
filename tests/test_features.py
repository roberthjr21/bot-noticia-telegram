import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock
import unittest
from zoneinfo import ZoneInfo

from bot_noticias.alerts import alert_score, choose_alert, in_quiet_hours
from bot_noticias.bot import schedule_user
from bot_noticias.config import Config
from bot_noticias.database import Database
from bot_noticias.diagnostics import diagnostic_text
from bot_noticias.news import NewsItem, fingerprint_for
from bot_noticias.summaries import article_summary, format_news_message, format_weekly_message
from bot_noticias.weekly import choose_weekly


def item(topic="ia", title="Anuncio oficial de nueva herramienta", age_hours=1, source="Diario"):
    return NewsItem(
        topic, title, "https://example.com/noticia", source,
        datetime.now(timezone.utc) - timedelta(hours=age_hours),
        "El medio publicó un fragmento del artículo.", fingerprint_for(title),
    )


class FeaturesTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.db = Database(Path(self.tempdir.name) / "news.sqlite3")
        self.db.subscribe(123, 123, "08:00")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_opt_in_defaults_persist(self):
        self.assertEqual(self.db.features(123), {
            "weekly_enabled": 0, "weekly_time": "18:00", "alerts_enabled": 0
        })
        self.db.set_feature(123, "weekly_enabled", True)
        self.db.set_feature(123, "alerts_enabled", True)
        self.db.set_feature(123, "weekly_time", "19:30")
        restored = Database(self.db.path)
        self.assertEqual(restored.features(123), {
            "weekly_enabled": 1, "weekly_time": "19:30", "alerts_enabled": 1
        })
        self.assertEqual(restored.get(123)["daily_time"], "08:00")
        self.assertEqual(restored.audio_settings(123)["audio_mode"], "normal")

    def test_invalid_settings_and_unregistered(self):
        with self.assertRaises(ValueError):
            self.db.set_feature(123, "weekly_time", "26:00")
        with self.assertRaises(ValueError):
            self.db.set_feature(123, "alerts_enabled", "yes")
        with self.assertRaises(ValueError):
            self.db.set_feature(123, "sql_injection", True)
        with self.assertRaises(ValueError):
            self.db.set_feature(999, "weekly_enabled", True)
        self.assertIsNone(self.db.features(999))

    def test_news_history_and_delivery_idempotence(self):
        one = item()
        self.db.record_news(123, [one, one])
        self.assertEqual(self.db.news_count(123), 1)
        self.assertEqual(len(self.db.weekly_news(123)), 1)
        self.assertTrue(self.db.claim_delivery(123, "alert", one.fingerprint))
        self.assertFalse(self.db.claim_delivery(123, "alert", one.fingerprint))
        self.db.mark_delivery(123, "alert", one.fingerprint)
        self.assertFalse(self.db.claim_delivery(123, "alert", one.fingerprint))
        self.assertEqual(self.db.sent_count_since(123, "alert", datetime.now(timezone.utc) - timedelta(days=1)), 1)
        self.assertEqual(self.db.latest_delivery(123)["kind"], "alert")

    def test_failed_claim_can_retry(self):
        self.assertTrue(self.db.claim_delivery(123, "weekly", "2026-W46"))
        self.db.release_delivery(123, "weekly", "2026-W46")
        self.assertTrue(self.db.claim_delivery(123, "weekly", "2026-W46"))

    def test_legacy_migration_preserves_data(self):
        import sqlite3
        path = Path(self.tempdir.name) / "legacy.sqlite3"
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE subscribers (chat_id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, daily_time TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, voice_enabled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            conn.execute("INSERT INTO subscribers(chat_id, user_id, daily_time, voice_enabled) VALUES (88, 88, '07:15', 1)")
        migrated = Database(path)
        self.assertEqual(migrated.features(88)["weekly_enabled"], 0)
        self.assertEqual(migrated.get(88)["daily_time"], "07:15")
        self.assertEqual(migrated.get(88)["voice_enabled"], 1)

    def test_weekly_job_only_when_opted_in(self):
        class Queue:
            def __init__(self):
                self.scheduled = []
            def get_jobs_by_name(self, name):
                return []
            def run_daily(self, callback, when, **kwargs):
                self.scheduled.append((kwargs["name"], kwargs))
        queue = Queue()
        config = Config(
            "placeholder", ZoneInfo("America/Bogota"), "America/Bogota", "08:00",
            self.db.path, frozenset()
        )
        app = SimpleNamespace(bot_data={"db": self.db, "config": config}, job_queue=queue)
        schedule_user(app, 123)
        self.assertEqual([name for name, _ in queue.scheduled], ["daily:123"])
        self.db.set_feature(123, "weekly_enabled", True)
        queue.scheduled.clear()
        schedule_user(app, 123)
        self.assertEqual([name for name, _ in queue.scheduled], ["daily:123", "weekly:123"])
        self.assertEqual(queue.scheduled[1][1]["days"], (0,))


class AlertsTests(unittest.TestCase):
    def test_quiet_hours(self):
        tz = ZoneInfo("America/Bogota")
        self.assertTrue(in_quiet_hours(datetime(2026, 11, 10, 23, tzinfo=tz)))
        self.assertTrue(in_quiet_hours(datetime(2026, 11, 10, 6, tzinfo=tz)))
        self.assertFalse(in_quiet_hours(datetime(2026, 11, 10, 7, tzinfo=tz)))
        self.assertFalse(in_quiet_hours(datetime(2026, 11, 10, 21, tzinfo=tz)))

    def test_relevant_and_recent(self):
        now = datetime.now(timezone.utc)
        candidate = item()
        self.assertGreaterEqual(alert_score(candidate, now), 3)
        self.assertEqual(alert_score(item(age_hours=20), now), 0)
        self.assertEqual(alert_score(item(source="Google News"), now), 0)
        self.assertIsNone(choose_alert([candidate], ["ia"], {candidate.fingerprint}, now))
        self.assertEqual(choose_alert([candidate], ["ia"], set(), now), candidate)
        self.assertIsNone(choose_alert([candidate], ["economia"], set(), now))


class SummariesTests(unittest.TestCase):
    def test_html_escaping_and_rss_disclaimer(self):
        n = item(title="<b>Prueba</b> & novedades")
        content = format_news_message([n], "America/Bogota", scheduled=True)
        self.assertIn("Prueba &amp; novedades", content)
        self.assertNotIn("<b>Prueba</b>", content)
        self.assertIn("Fragmento de la fuente", content)
        self.assertIn("Google News RSS", content)
        self.assertIn("https://example.com/noticia", content)

    def test_missing_excerpt_omits_repeated_placeholder(self):
        n = item()
        no_excerpt = NewsItem(n.topic, n.title, n.url, n.source, n.published, "", n.fingerprint)
        self.assertEqual(article_summary(no_excerpt), "")
        message = format_news_message([no_excerpt], "America/Bogota")
        self.assertNotIn("El RSS no incluye más detalles", message)
        self.assertIn("Leer la fuente", message)

    def test_related_headlines_labeled(self):
        n = item()
        with_related = NewsItem(n.topic, n.title, n.url, n.source, n.published, "", n.fingerprint,
                                ("Otra noticia relacionada", "Otro titular sobre el tema"))
        message = format_news_message([with_related], "America/Bogota")
        self.assertIn("Otros titulares relacionados en RSS", message)
        self.assertIn("Otra noticia relacionada", message)
        self.assertNotIn("Qué se sabe", message)

    def test_weekly_balances_categories(self):
        rows = [
            {"topic": "ia", "published_at": "2026-11-10T09:00:00", "title": "A"},
            {"topic": "ia", "published_at": "2026-11-09T09:00:00", "title": "B"},
            {"topic": "programacion", "published_at": "2026-11-10T08:00:00", "title": "C"},
        ]
        chosen = choose_weekly(rows, limit=2)
        self.assertEqual({x["topic"] for x in chosen}, {"ia", "programacion"})

    def test_weekly_escapes_content(self):
        row = {"topic": "ia", "title": "A & B", "source": "Ejemplo", "description": "Texto",
               "url": "https://example.com/a", "published_at": "2026-11-10T09:00:00"}
        self.assertIn("A &amp; B", format_weekly_message([row], "America/Bogota"))

    def test_diagnostic_hides_secrets(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "test.sqlite3")
            db.subscribe(1, 1, "08:00")
            queue = SimpleNamespace(jobs=lambda: [SimpleNamespace(name="daily:1")])
            app = SimpleNamespace(job_queue=queue, bot_data={"rss_status": {"at": "Hoy", "feeds_ok": 2, "feeds_failed": 1}})
            report = diagnostic_text(db, 1, app, "America/Bogota")
            self.assertIn("Diagnóstico", report)
            self.assertIn("2 / 1", report)
            self.assertNotIn("TELEGRAM_BOT_TOKEN", report)


if __name__ == "__main__":
    unittest.main()

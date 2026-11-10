"""Pruebas de integración sin red: envío, deduplicación y jobs programados."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest
from zoneinfo import ZoneInfo

from bot_noticias.bot import send_alerts_job, send_weekly_job, send_digest, schedule_user
from bot_noticias.config import Config
from bot_noticias.database import Database
from bot_noticias.news import NewsItem, NewsResult, fingerprint_for


def make_item(index: int) -> NewsItem:
    title = f"Anuncio oficial de herramienta nueva {index}"
    return NewsItem(
        "ia", title, f"https://example.com/{index}", "Diario",
        datetime.now(timezone.utc) - timedelta(minutes=15),
        "Detalles de prueba del RSS.", fingerprint_for(title),
    )


class ScheduledJobsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = TemporaryDirectory()
        self.db = Database(Path(self.tempdir.name) / "news.sqlite3")
        self.db.subscribe(1, 1, "08:00")
        self.config = Config(
            "token-de-prueba", ZoneInfo("America/Bogota"), "America/Bogota",
            "08:00", self.db.path, frozenset({1})
        )
        self.bot = SimpleNamespace(send_message=AsyncMock())
        self.app = SimpleNamespace(bot_data={"db": self.db, "config": self.config})
        self.context = SimpleNamespace(application=self.app, bot=self.bot, job=SimpleNamespace(data=1))

    async def asyncTearDown(self):
        self.tempdir.cleanup()

    async def test_daily_digest_records_history(self):
        one = make_item(1)
        with patch("bot_noticias.bot.get_news", new_callable=AsyncMock, return_value=NewsResult([one], 1, 0)):
            await send_digest(self.context, 1, ["ia"], scheduled=True)
        self.bot.send_message.assert_awaited_once()
        self.assertEqual(self.db.news_count(1), 1)
        self.assertTrue(self.db.was_delivered(1, one.fingerprint))
        self.assertEqual(self.db.latest_delivery(1)["kind"], "daily")

    async def test_alerts_are_opt_in_and_not_repeated(self):
        self.db.set_feature(1, "alerts_enabled", True)
        one = make_item(1)
        with patch("bot_noticias.bot.in_quiet_hours", return_value=False), patch(
            "bot_noticias.bot.get_news", new_callable=AsyncMock,
            return_value=NewsResult([one], 1, 0)
        ) as fetch:
            await send_alerts_job(self.context)
            await send_alerts_job(self.context)
        self.assertEqual(self.bot.send_message.await_count, 1)
        self.assertEqual(fetch.await_count, 2)
        self.assertTrue(self.db.was_delivered(1, one.fingerprint))
        self.assertEqual(self.db.latest_delivery(1)["kind"], "alert")

    async def test_alerts_obey_quiet_hours(self):
        self.db.set_feature(1, "alerts_enabled", True)
        with patch("bot_noticias.bot.in_quiet_hours", return_value=True), patch(
            "bot_noticias.bot.get_news", new_callable=AsyncMock
        ) as fetch:
            await send_alerts_job(self.context)
        fetch.assert_not_awaited()

    async def test_weekly_only_once(self):
        self.db.set_feature(1, "weekly_enabled", True)
        self.db.record_news(1, [make_item(i) for i in range(3)])
        await send_weekly_job(self.context)
        await send_weekly_job(self.context)
        self.assertEqual(self.bot.send_message.await_count, 1)
        self.assertEqual(self.db.latest_delivery(1)["kind"], "weekly")

    async def test_scheduled_jobs_respect_allowed_users(self):
        class Queue:
            def __init__(self):
                self.names = []
            def get_jobs_by_name(self, name):
                return []
            def run_daily(self, callback, when, **kwargs):
                self.names.append(kwargs["name"])
        queue = Queue()
        self.db.subscribe(2, 2, "08:00")
        self.db.set_feature(2, "weekly_enabled", True)
        self.db.set_feature(2, "alerts_enabled", True)
        self.app.job_queue = queue
        schedule_user(self.app, 2)
        self.assertEqual(queue.names, [])
        with patch("bot_noticias.bot.in_quiet_hours", return_value=False), patch(
            "bot_noticias.bot.get_news", new_callable=AsyncMock,
            return_value=NewsResult([make_item(1)], 1, 0)
        ):
            await send_alerts_job(self.context)
        self.assertEqual(self.bot.send_message.await_count, 0)


if __name__ == "__main__":
    unittest.main()

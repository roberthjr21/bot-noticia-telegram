import asyncio
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from bot_noticias.audio_settings import AUDIO_RATES, AUDIO_VOICES, resolve_voice
from bot_noticias.database import Database
from bot_noticias.news import NewsItem
from bot_noticias.voice import speech_text, send_spoken_news


class AudioSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.db = Database(Path(self.tempdir.name) / "test.sqlite3")
        self.db.subscribe(123, 123, "08:00")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_defaults_and_persistence(self):
        self.assertEqual(self.db.audio_settings(123), {
            "audio_mode": "normal", "voice_key": "predeterminada", "rate_key": "normal"
        })
        self.db.set_audio_setting(123, "audio_mode", "detallado")
        self.db.set_audio_setting(123, "voice_key", "gonzalo")
        self.db.set_audio_setting(123, "rate_key", "lenta")
        restored = Database(self.db.path)
        self.assertEqual(restored.audio_settings(123), {
            "audio_mode": "detallado", "voice_key": "gonzalo", "rate_key": "lenta"
        })
        self.assertEqual(restored.get(123)["daily_time"], "08:00")

    def test_invalid_values_and_missing_subscriber(self):
        for field, value in [("audio_mode", "infinito"), ("voice_key", "evil"), ("rate_key", "+999%"), ("x = 1", "normal")]:
            with self.assertRaises(ValueError):
                self.db.set_audio_setting(123, field, value)
        with self.assertRaises(ValueError):
            self.db.set_audio_setting(999, "audio_mode", "rapido")
        self.assertIsNone(self.db.audio_settings(999))

    def test_existing_subscriber_migration(self):
        path = Path(self.tempdir.name) / "legacy.sqlite3"
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE subscribers (chat_id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, daily_time TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, voice_enabled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            conn.execute("INSERT INTO subscribers(chat_id, user_id, daily_time, voice_enabled) VALUES (77, 77, '07:45', 1)")
        db = Database(path)
        self.assertEqual(db.audio_settings(77)["audio_mode"], "normal")
        self.assertEqual(db.get(77)["voice_enabled"], 1)
        self.assertEqual(db.get(77)["daily_time"], "07:45")

    def test_voice_resolver(self):
        self.assertEqual(resolve_voice("predeterminada", "es-CO-SalomeNeural"), "es-CO-SalomeNeural")
        self.assertEqual(resolve_voice("gonzalo", "es-CO-SalomeNeural"), AUDIO_VOICES["gonzalo"][1])
        self.assertEqual(AUDIO_RATES["rapida"][1], "+15%")
        with self.assertRaises(ValueError):
            resolve_voice("desconocida", "es-CO-SalomeNeural")

    def test_three_speech_modes(self):
        items = [NewsItem("ia", f"Noticia de prueba {i}", "https://example.com", "Medio", datetime.now(timezone.utc), "Descripción verificada del RSS", str(i)) for i in range(5)]
        quick = speech_text(items, mode="rapido")
        normal = speech_text(items, mode="normal")
        detailed = speech_text(items, mode="detallado")
        self.assertIn("Noticia 3", quick)
        self.assertNotIn("Noticia 4", quick)
        self.assertNotIn("Descripción", quick)
        self.assertIn("Descripción", normal)
        self.assertIn("Medio:", detailed)
        self.assertGreater(len(detailed), len(normal))
        self.assertLessEqual(len(quick), 700)
        self.assertLessEqual(len(normal), 1900)
        self.assertLessEqual(len(detailed), 3500)
        with self.assertRaises(ValueError):
            speech_text(items, mode="inventado")

    def test_tts_receives_selected_voice_mode_and_speed(self):
        sent = {}
        def communicate(script, voice, rate):
            sent.update(script=script, voice=voice, rate=rate)
            async def save(path):
                Path(path).write_bytes(b"fake-mp3")
            return SimpleNamespace(save=save)
        bot = SimpleNamespace(send_audio=AsyncMock())
        context = SimpleNamespace(bot=bot)
        item = NewsItem("ia", "Nueva herramienta", "https://example.com", "Medio", datetime.now(timezone.utc), "Resumen de prueba", "h")
        with patch.dict(sys.modules, {"edge_tts": SimpleNamespace(Communicate=communicate)}), patch("bot_noticias.voice.shutil.which", return_value=None):
            asyncio.run(send_spoken_news(context, 123, [item], scheduled=False, voice_name="es-CO-GonzaloNeural", mode="rapido", rate="+15%"))
        self.assertEqual(sent["voice"], "es-CO-GonzaloNeural")
        self.assertEqual(sent["rate"], "+15%")
        self.assertNotIn("Resumen de prueba", sent["script"])
        bot.send_audio.assert_awaited_once()

    def test_tts_rejects_unknown_speed(self):
        with self.assertRaises(ValueError):
            asyncio.run(send_spoken_news(SimpleNamespace(), 123, [], scheduled=False, voice_name="es-CO-SalomeNeural", rate="+999%"))


if __name__ == "__main__":
    unittest.main()

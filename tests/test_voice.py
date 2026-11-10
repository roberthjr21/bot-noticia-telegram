from datetime import datetime, timezone
import unittest

from bot_noticias.news import NewsItem
from bot_noticias.voice import speech_text


class VoiceTests(unittest.TestCase):
    def test_speech_reads_headlines_without_urls(self):
        item = NewsItem(
            "ia", "Nuevo modelo de IA", "https://example.com/noticia", "Diario",
            datetime.now(timezone.utc), "Se anunció hoy", "hash",
        )
        script = speech_text([item])
        self.assertIn("Nuevo modelo de IA", script)
        self.assertIn("Inteligencia artificial", script)
        self.assertNotIn("https://", script)

    def test_speech_limits_items_and_length(self):
        item = NewsItem("tecnologia", "A" * 5000, "https://example.com", "Medio", None, "B" * 5000, "hash")
        script = speech_text([item] * 8, scheduled=True)
        self.assertIn("resumen diario", script)
        self.assertNotIn("Noticia 6", script)
        self.assertLessEqual(len(script), 3500)


if __name__ == "__main__":
    unittest.main()

import asyncio
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from bot_noticias.news import (
    NewsItem, TOPICS, clean_text, fingerprint_for, get_news, parse_rss, rss_details, rss_url,
)


class NewsTests(unittest.TestCase):
    def test_google_news_rss_parse(self):
        content = b'''<?xml version="1.0" encoding="UTF-8"?>
        <rss><channel><item>
          <title>Actualizaci&#243;n de Python - Diario Ejemplo</title>
          <link>https://news.google.com/rss/articles/abc</link>
          <pubDate>Mon, 09 Nov 2026 10:30:00 GMT</pubDate>
          <source url="https://ejemplo.com">Diario Ejemplo</source>
          <description>&lt;p&gt;Python lanza una nueva actualizaci&#243;n.&lt;/p&gt;</description>
        </item></channel></rss>'''
        news = parse_rss(content, "programacion")
        self.assertEqual(len(news), 1)
        self.assertEqual(news[0].title, "Actualización de Python")
        self.assertEqual(news[0].source, "Diario Ejemplo")
        self.assertEqual(news[0].description, "Python lanza una nueva actualización.")
        self.assertEqual(news[0].published.year, 2026)

    def test_related_rss_headlines_not_mistaken_for_excerpt(self):
        xml = """<rss><channel><item>
          <title>Nuevo videojuego anunciado - Medio A</title>
          <link>https://news.google.com/rss/articles/abc</link>
          <source>Medio A</source>
          <description>&lt;ol&gt;
            &lt;li&gt;&lt;a&gt;Nuevo videojuego anunciado&lt;/a&gt;&lt;/li&gt;
            &lt;li&gt;&lt;a&gt;El estudio anuncia una nueva fecha de salida&lt;/a&gt;&lt;/li&gt;
            &lt;li&gt;&lt;a&gt;El juego muestra su primer avance oficial&lt;/a&gt;&lt;/li&gt;
          &lt;/ol&gt;</description>
        </item></channel></rss>""".encode()
        parsed = parse_rss(xml, "videojuegos")[0]
        self.assertEqual(parsed.description, "")
        self.assertEqual(parsed.related_headlines, (
            "El estudio anuncia una nueva fecha de salida",
            "El juego muestra su primer avance oficial",
        ))

    def test_rss_without_excerpt_or_related(self):
        self.assertEqual(
            rss_details("<ol><li><a>Un titular repetido</a></li></ol>",
                        "Un titular repetido", "Medio"),
            ("", ()),
        )

    def test_rss_paragraph_kept(self):
        excerpt, related = rss_details(
            "<p>El fabricante presentó dos dispositivos durante el evento.</p>",
            "Nuevos dispositivos", "Medio",
        )
        self.assertEqual(excerpt, "El fabricante presentó dos dispositivos durante el evento.")
        self.assertEqual(related, ())

    def test_invalid_url_ignored(self):
        xml = b'<rss><channel><item><title>x</title><link>javascript:alert(1)</link></item></channel></rss>'
        self.assertEqual(parse_rss(xml, "ia"), [])

    def test_clean_title_normalizes_accents(self):
        self.assertEqual(fingerprint_for("Programación: IA!"), fingerprint_for("programacion IA"))

    def test_rss_url_encodes_search(self):
        url = rss_url("videojuegos")
        self.assertIn("news.google.com/rss/search", url)
        self.assertIn("ceid=CO%3Aes", url)

    def test_clean_text_handles_html(self):
        self.assertEqual(clean_text("&lt;b&gt;Hola&lt;/b&gt; &amp; adi&#243;s"), "Hola & adiós")

    def test_balanced_feed_selection_and_exclusion(self):
        def article(topic, title, age):
            return NewsItem(
                topic, title, "https://example.com/test", "Ejemplo",
                datetime.now(timezone.utc) - timedelta(hours=age),
                "", fingerprint_for(title),
            )
        fixtures = {
            "ia": [article("ia", "Modelo A", 1), article("ia", "Modelo B", 2)],
            "programacion": [article("programacion", "Software A", 3)],
            "videojuegos": [article("videojuegos", "Juego viejo", 100)],
        }
        with patch("bot_noticias.news.fetch_topic", side_effect=lambda topic: fixtures[topic]):
            result = asyncio.run(get_news(
                ["ia", "programacion", "videojuegos"],
                excluded_fingerprints={fingerprint_for("Modelo A")},
            ))
        self.assertEqual([item.title for item in result.items], ["Modelo B", "Software A"])
        self.assertEqual(result.feeds_ok, 3)

    def test_topics_available(self):
        self.assertIn("colombia", TOPICS)
        self.assertIn("ciberseguridad", TOPICS)


if __name__ == "__main__":
    unittest.main()

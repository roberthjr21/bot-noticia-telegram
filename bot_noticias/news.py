"""Lectura de RSS de Google News. No usa IA, scraping de artículos ni APIs pagas."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import html
import logging
import re
from typing import Iterable
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
import unicodedata
import xml.etree.ElementTree as ET

log = logging.getLogger(__name__)

# Se mantienen búsquedas simples para que Google News RSS devuelva resultados en español.
TOPICS = OrderedDict([
    ("ia", ("🤖", "Inteligencia artificial", '"inteligencia artificial" OR OpenAI OR Anthropic OR Gemini')),
    ("programacion", ("💻", "Programación y desarrollo web", '"desarrollo de software" OR Python OR JavaScript OR React OR GitHub')),
    ("tecnologia", ("📱", "Tecnología", 'tecnología OR dispositivos OR Microsoft OR Apple OR Google')),
    ("videojuegos", ("🎮", "Videojuegos, PlayStation y GTA", '"PlayStation" OR "PS5" OR "GTA VI" OR "videojuegos"')),
    ("ciberseguridad", ("🛡️", "Ciberseguridad", '"ciberseguridad" OR "vulnerabilidad" OR "ransomware"')),
    ("colombia", ("🇨🇴", "Colombia", 'Colombia noticias actualidad')),
    ("mundo", ("🌍", "Mundo", 'internacional noticias actualidad')),
    ("ciencia", ("🔬", "Ciencia", 'ciencia OR espacio OR investigación científica')),
    ("economia", ("💰", "Economía", 'economía OR inflación OR mercado financiero')),
])

DEFAULT_TOPICS = ("ia", "programacion", "tecnologia", "videojuegos", "colombia")


@dataclass(frozen=True)
class NewsItem:
    topic: str
    title: str
    url: str
    source: str
    published: datetime | None
    description: str
    fingerprint: str


def clean_text(raw: str | None, max_chars: int = 220) -> str:
    if not raw:
        return ""
    text = html.unescape(raw)
    text = re.sub(r"<[^>]*>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"
    return text


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKD", title.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def fingerprint_for(title: str) -> str:
    return hashlib.sha256(normalize_title(title).encode("utf-8")).hexdigest()


def rss_url(topic: str) -> str:
    if topic not in TOPICS:
        raise ValueError("Categoría desconocida")
    query = f"({TOPICS[topic][2]}) when:3d"
    params = urlencode({"q": query, "hl": "es", "gl": "CO", "ceid": "CO:es"})
    return f"https://news.google.com/rss/search?{params}"


def parse_rss(xml_data: bytes, topic: str) -> list[NewsItem]:
    root = ET.fromstring(xml_data)
    articles: list[NewsItem] = []
    for node in root.findall("./channel/item")[:30]:
        raw_title = clean_text(node.findtext("title"), 250)
        link = (node.findtext("link") or "").strip()
        parsed_link = urlparse(link)
        if parsed_link.scheme != "https" or not parsed_link.netloc:
            continue
        source = clean_text(node.findtext("source"), 70) or "Google News"
        title = raw_title
        if source != "Google News" and title.lower().endswith(" - " + source.lower()):
            title = title[: -(len(source) + 3)].strip()
        if not title:
            continue
        raw_date = node.findtext("pubDate")
        published = None
        if raw_date:
            try:
                published = parsedate_to_datetime(raw_date)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                published = published.astimezone(timezone.utc)
            except (TypeError, ValueError, IndexError):
                pass
        desc = clean_text(node.findtext("description"), 185)
        if normalize_title(title) in normalize_title(desc):
            desc = ""  # No se confunde el título duplicado con un resumen periodístico.
        articles.append(
            NewsItem(topic, title, link, source, published, desc, fingerprint_for(title))
        )
    return articles


def fetch_topic(topic: str) -> list[NewsItem]:
    request = Request(
        rss_url(topic),
        headers={"User-Agent": "NewsBotTelegram/1.0 (+RSS; personal)", "Accept": "application/rss+xml, application/xml, text/xml"},
    )
    with urlopen(request, timeout=12) as response:
        content = response.read(1_500_001)
    if len(content) > 1_500_000:
        raise ValueError("RSS excedió el tamaño máximo permitido")
    return parse_rss(content, topic)


@dataclass(frozen=True)
class NewsResult:
    items: list[NewsItem]
    feeds_ok: int
    feeds_failed: int


async def get_news(
    topics: Iterable[str],
    limit: int = 5,
    excluded_fingerprints: set[str] | None = None,
    max_age_hours: int = 72,
) -> NewsResult:
    """Consulta feeds concurrentemente y distribuye la selección entre temas.

    Un feed caído no impide mostrar noticias de otros temas. Nunca devuelve
    noticias sin fecha o antiguas como si fueran actuales.
    """
    selected = list(dict.fromkeys(topic for topic in topics if topic in TOPICS))
    if not selected:
        return NewsResult([], 0, 0)

    gate = asyncio.Semaphore(4)

    async def fetch_one(topic: str):
        async with gate:
            try:
                return await asyncio.to_thread(fetch_topic, topic)
            except Exception as exc:
                log.warning("No se pudo leer RSS de %s: %s", topic, exc)
                return None

    results = await asyncio.gather(*(fetch_one(topic) for topic in selected))
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max_age_hours)
    excluded = excluded_fingerprints or set()
    feeds_ok = sum(result is not None for result in results)
    feeds_failed = len(results) - feeds_ok
    groups: dict[str, list[NewsItem]] = {}
    for topic, result in zip(selected, results):
        valid = [
            article for article in result or []
            if article.published is not None
            and cutoff <= article.published <= now + timedelta(minutes=10)
            and article.fingerprint not in excluded
        ]
        groups[topic] = sorted(valid, key=lambda article: article.published or now, reverse=True)

    chosen: list[NewsItem] = []
    dedup = set(excluded)
    # Primera ronda: una noticia por categoría. Rondas posteriores: variedad.
    while len(chosen) < limit and any(groups.values()):
        progress = False
        for topic in selected:
            queue = groups[topic]
            while queue:
                candidate = queue.pop(0)
                if candidate.fingerprint in dedup:
                    continue
                chosen.append(candidate)
                dedup.add(candidate.fingerprint)
                progress = True
                break
            if len(chosen) >= limit:
                break
        if not progress:
            break
    return NewsResult(chosen, feeds_ok, feeds_failed)

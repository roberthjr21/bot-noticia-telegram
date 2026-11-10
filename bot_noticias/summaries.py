"""Resúmenes trazables: únicamente metadatos y fragmentos RSS, sin inventar hechos."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
from html import escape

from .news import NewsItem, TOPICS, clean_text


def article_summary(item: NewsItem) -> str:
    """Extrae un fragmento de RSS. No atribuye opiniones ni causalidades inexistentes."""
    snippet = clean_text(item.description, 250)
    title = clean_text(item.title, 240)
    if not snippet or snippet.casefold() in title.casefold():
        return "El RSS no incluye más detalles. Consulta la fuente original."
    return snippet


def format_news_message(items: list[NewsItem], timezone_name: str, *, scheduled: bool = False) -> str:
    date_label = datetime.now(ZoneInfo(timezone_name)).strftime("%d/%m/%Y")
    heading = "☀️ <b>Tu resumen diario</b>" if scheduled else "🗞 <b>Noticias para ti</b>"
    count_label = "noticia" if len(items) == 1 else "noticias"
    lines = [f"{heading} · {date_label}", f"<i>{len(items)} {count_label} · Zona: {escape(timezone_name)}</i>"]
    for i, item in enumerate(items, 1):
        emoji, category, _ = TOPICS[item.topic]
        lines.append(f"\n<b>{i}. {escape(clean_text(item.title, 240))}</b>")
        lines.append(f"{emoji} {escape(category)} · {escape(item.source)}")
        if item.published:
            lines.append(f"<i>Publicado: {item.published:%d/%m/%Y %H:%M} UTC</i>")
        lines.append(f"📝 <b>Qué se sabe:</b> {escape(article_summary(item))}")
        lines.append(f'<a href="{escape(item.url, quote=True)}">🔗 Leer la fuente</a>')
    lines.append("\n<i>Resúmenes basados en titulares y fragmentos RSS, no en artículos completos. "
                 "Verifica los detalles en cada enlace.</i>")
    return "\n".join(lines)


def format_weekly_message(rows: list[dict], timezone_name: str) -> str:
    lines = ["📅 <b>Tu resumen semanal</b>",
             f"<i>{len(rows)} noticias registradas durante los últimos 7 días · {escape(timezone_name)}</i>"]
    for index, item in enumerate(rows, 1):
        emoji, category, _ = TOPICS.get(item["topic"], ("📰", "Actualidad", ""))
        lines.append(f"\n<b>{index}. {escape(clean_text(item['title'], 230))}</b>")
        lines.append(f"{emoji} {escape(category)} · {escape(item['source'])}")
        if item.get("description"):
            lines.append(f"📝 {escape(clean_text(item['description'], 160))}")
        lines.append(f'<a href="{escape(item["url"], quote=True)}">🔗 Ver noticia</a>')
    lines.append("\n<i>Selección del historial guardado por este bot; no representa toda la actualidad semanal. "
                 "Consulta las fuentes originales.</i>")
    return "\n".join(lines)

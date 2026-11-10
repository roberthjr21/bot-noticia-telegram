"""Criterios conservadores para alertas de novedades destacadas, no breaking news."""

from __future__ import annotations

from datetime import datetime, timedelta
from .news import NewsItem, normalize_title

# Evitar el uso de la palabra «urgente» para noticias no verificadas.
STRONG_TERMS = (
    "vulnerabilidad critica", "brecha de seguridad", "actualizacion de seguridad",
    "anuncio oficial", "lanzamiento oficial", "fecha de lanzamiento",
    "nuevo modelo", "nuevo juego", "parche de seguridad",
    "filtracion de datos", "se confirma",
)
MEDIUM_TERMS = (
    "anuncia", "anuncio", "lanzamiento", "presenta", "confirma",
    "actualiza", "publica", "disponible", "estrena",
)


def in_quiet_hours(local_now: datetime) -> bool:
    """No enviar avisos de 22:00 a 06:59, hora local del bot."""
    return local_now.hour >= 22 or local_now.hour < 7


def alert_score(item: NewsItem, now: datetime) -> int:
    if not item.published or not item.source or item.source == "Google News":
        return 0
    age = now - item.published.astimezone(now.tzinfo)
    if age < timedelta(minutes=-10) or age > timedelta(hours=4):
        return 0
    title = normalize_title(item.title)
    score = 0
    if any(term in title for term in STRONG_TERMS):
        score += 3
    if any(term in title for term in MEDIUM_TERMS):
        score += 1
    if item.topic in ("ia", "programacion", "ciberseguridad", "videojuegos"):
        score += 1
    return score


def choose_alert(items: list[NewsItem], topics: list[str], excluded: set[str], now: datetime) -> NewsItem | None:
    """Selecciona una novedad destacada reciente, sin repetir titulares ya enviados."""
    choices = [
        item for item in items
        if item.topic in topics and item.fingerprint not in excluded and alert_score(item, now) >= 3
    ]
    if not choices:
        return None
    return max(choices, key=lambda item: (alert_score(item, now), item.published))


def alert_message(item: NewsItem) -> str:
    from html import escape
    from .summaries import article_summary
    excerpt = article_summary(item)
    if excerpt:
        detail = f"📝 Fragmento de la fuente: {escape(excerpt)}\n"
    elif item.related_headlines:
        detail = "🗞 Otros titulares en RSS: " + "; ".join(
            escape(headline) for headline in item.related_headlines
        ) + "\n"
    else:
        detail = ""
    return (
        "🔔 <b>Novedad destacada</b>\n\n"
        f"<b>{escape(item.title)}</b>\n"
        f"Medio: {escape(item.source)}\n"
        f"{detail}"
        f'<a href="{escape(item.url, quote=True)}">🔗 Leer la noticia</a>\n\n'
        "<i>Selección automática por palabras clave. No es una alerta de emergencia ni "
        "una confirmación independiente del contenido.</i>"
    )

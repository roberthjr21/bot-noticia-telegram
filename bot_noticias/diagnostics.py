"""Diagnóstico limitado al suscriptor autorizado, sin secretos ni datos sensibles."""

from __future__ import annotations

from datetime import datetime
from html import escape


def diagnostic_text(db, chat_id: int, app, timezone_name: str) -> str:
    features = db.features(chat_id)
    subscriber = db.get(chat_id)
    if not subscriber:
        return "Primero envía /start."
    queue = app.job_queue
    jobs = queue.jobs() if queue else ()
    names = {job.name for job in jobs}
    daily = f"daily:{chat_id}" in names
    weekly = f"weekly:{chat_id}" in names
    alerts = "alerts:scan" in names
    last = db.latest_delivery(chat_id)
    last_label = "Sin registros" if not last else f"{last['kind']} · {last['sent_at'] or last['created_at']}"
    rss = app.bot_data.get("rss_status") or {}
    checked = rss.get("at") or "Sin comprobaciones todavía"
    feeds_ok = rss.get("feeds_ok", "—")
    feeds_failed = rss.get("feeds_failed", "—")
    return (
        "<b>🩺 Diagnóstico de NewsBot</b>\n\n"
        f"Suscripción: {'Activa' if subscriber['enabled'] else 'Pausada'}\n"
        f"Programación diaria: {'OK' if daily else 'No programada'}\n"
        f"Resumen semanal: {'OK' if weekly else 'Desactivado'}\n"
        f"Comprobador de alertas: {'OK' if alerts else 'No programado'}\n"
        f"Alertas personales: {'Activadas' if features['alerts_enabled'] else 'Desactivadas'}\n"
        f"Noticias guardadas (7 días): {db.news_count(chat_id, days=7)}\n"
        f"Último envío registrado: {escape(last_label)}\n"
        f"Última consulta RSS: {escape(str(checked))}\n"
        f"Fuentes correctas / fallidas: {feeds_ok} / {feeds_failed}\n"
        f"Zona horaria: {escape(timezone_name)}\n\n"
        "<i>Este diagnóstico no puede comprobar si Fedora está apagado o sin conexión.</i>"
    )

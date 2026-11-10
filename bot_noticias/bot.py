"""Comandos y botones del bot de noticias Telegram."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from html import escape
import logging

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.error import Forbidden, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from .audio_settings import AUDIO_MODES, AUDIO_RATES, AUDIO_VOICES, audio_keyboard, audio_text, resolve_voice
from .config import Config, validate_time
from .database import Database
from .news import TOPICS, NewsItem, get_news
from .voice import send_spoken_news
from .summaries import format_news_message, format_weekly_message
from .weekly import choose_weekly
from .alerts import choose_alert, in_quiet_hours, alert_message
from .diagnostics import diagnostic_text

logger = logging.getLogger(__name__)


def get_db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["db"]


def get_config(context: ContextTypes.DEFAULT_TYPE) -> Config:
    return context.application.bot_data["config"]


def is_authorized(update: Update, config: Config) -> bool:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or chat.type != ChatType.PRIVATE:
        return False
    return not config.allowed_user_ids or user.id in config.allowed_user_ids


async def check_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if is_authorized(update, get_config(context)):
        return True
    if update.callback_query:
        await update.callback_query.answer("Este bot solo está habilitado para usuarios autorizados y chats privados.", show_alert=True)
    elif update.effective_message:
        await update.effective_message.reply_text(
            "🔒 Este bot solo funciona por chat privado y para usuarios autorizados. "
            "Envía /miid para conocer tu ID de Telegram."
        )
    return False


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗞 Ver noticias ahora", callback_data="news:all")],
        [InlineKeyboardButton("🎯 Mis temas", callback_data="menu:topics"),
         InlineKeyboardButton("⏰ Hora del resumen", callback_data="menu:time")],
        [InlineKeyboardButton("🎙 Audio y voces", callback_data="menu:audio"),
         InlineKeyboardButton("🎧 Escuchar noticias", callback_data="news:listen")],
        [InlineKeyboardButton("🔔 Alertas importantes", callback_data="menu:alerts"),
         InlineKeyboardButton("📅 Resumen semanal", callback_data="menu:weekly")],
        [InlineKeyboardButton("📋 Mi configuración", callback_data="menu:status"),
         InlineKeyboardButton("🩺 Diagnóstico", callback_data="menu:diagnostics")],
        [InlineKeyboardButton("⏯ Pausar / activar", callback_data="menu:toggle")],
    ])


def topics_keyboard(active: list[str]) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            ("✅ " if topic in active else "➕ ") + label,
            callback_data=f"topic:{topic}",
        )
        for topic, (_, label, _) in TOPICS.items()
    ]
    rows = [[button] for button in buttons]
    rows.append([InlineKeyboardButton("🏠 Volver al menú", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


def times_keyboard(current_time: str) -> InlineKeyboardMarkup:
    suggestions = ["07:00", "08:00", "09:00", "12:00", "18:00", "21:00"]
    buttons = [
        InlineKeyboardButton(("✅ " if clock == current_time else "🕒 ") + clock,
                             callback_data=f"time:{clock}")
        for clock in suggestions
    ]
    return InlineKeyboardMarkup([
        buttons[:3], buttons[3:],
        [InlineKeyboardButton("🏠 Volver al menú", callback_data="menu:home")],
    ])


def alerts_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔕 Desactivar alertas" if enabled else "🔔 Activar alertas",
                              callback_data="alerts:toggle")],
        [InlineKeyboardButton("🏠 Volver al menú", callback_data="menu:home")],
    ])


def weekly_keyboard(enabled: bool, weekly_time: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏸ Desactivar semanal" if enabled else "📅 Activar semanal",
                              callback_data="weekly:toggle")],
        [InlineKeyboardButton(("✅ " if weekly_time == clock else "🕒 ") + clock,
                              callback_data=f"weekly:time:{clock}") for clock in ("12:00", "18:00", "21:00")],
        [InlineKeyboardButton("📖 Ver resumen disponible", callback_data="weekly:preview")],
        [InlineKeyboardButton("🏠 Volver al menú", callback_data="menu:home")],
    ])


def alerts_text(enabled: bool) -> str:
    return (
        "<b>🔔 Alertas de noticias destacadas</b>\n\n"
        f"Estado: {'🟢 Activadas' if enabled else '🔕 Desactivadas'}\n"
        "Hasta 2 alertas al día, solo entre las 07:00 y las 21:59 (hora del bot). "
        "Se eligen automáticamente por palabras clave y actualidad; "
        "no son avisos de emergencia ni verificaciones independientes."
    )


def weekly_text(enabled: bool, weekly_time: str, timezone_name: str) -> str:
    return (
        "<b>📅 Resumen semanal</b>\n\n"
        f"Estado: {'🟢 Activado' if enabled else '⏸ Desactivado'}\n"
        f"Envío: domingo a las <b>{escape(weekly_time)}</b> ({escape(timezone_name)}).\n"
        "Incluye noticias guardadas por el bot durante los últimos 7 días. "
        "Al activar la función por primera vez, el historial puede ser insuficiente. "
        "Usa /semanal HH:MM para cambiar la hora."
    )


def format_topics(active: list[str]) -> str:
    return ", ".join(f"{TOPICS[t][0]} {TOPICS[t][1]}" for t in active) or "Ninguna"


def status_text(db: Database, config: Config, chat_id: int) -> str:
    subscriber = db.get(chat_id)
    if not subscriber:
        return "Primero envía /start para configurar tu resumen."
    status = "🟢 Activado" if subscriber["enabled"] else "⏸ Pausado"
    audio = db.audio_settings(chat_id)
    features = db.features(chat_id)
    return (
        "<b>⚙️ Tu configuración</b>\n\n"
        f"Estado: {status}\n"
        f"Lectura en voz alta: {'🔊 Activada' if subscriber.get('voice_enabled') else '🔇 Desactivada'}\n"
        f"Modo de audio: {escape(AUDIO_MODES[audio['audio_mode']][0])}\n"
        f"Voz: {escape(AUDIO_VOICES[audio['voice_key']][0])}\n"
        f"Velocidad: {escape(AUDIO_RATES[audio['rate_key']][0])}\n"
        f"Envío diario: <b>{subscriber['daily_time']}</b>\n"
        f"Alertas: {'🔔 Activadas' if features['alerts_enabled'] else '🔕 Desactivadas'}\n"
        f"Semanal: {'📅 Activado' if features['weekly_enabled'] else '⏸ Desactivado'} "
        f"(domingo {escape(features['weekly_time'])})\n"
        f"Zona horaria: <b>{escape(config.timezone_name)}</b>\n"
        f"Temas: {escape(format_topics(db.topics(chat_id)))}\n\n"
        "Puedes cambiar la hora con <code>/hora 08:00</code>."
    )


def schedule_user(app: Application, chat_id: int) -> None:
    """Reemplaza el trabajo del chat, sin duplicar notificaciones."""
    db: Database = app.bot_data["db"]
    config: Config = app.bot_data["config"]
    queue = app.job_queue
    if queue is None:
        raise RuntimeError('Falta el extra "job-queue" de python-telegram-bot')
    name = f"daily:{chat_id}"
    for job in queue.get_jobs_by_name(name):
        job.schedule_removal()
    subscriber = db.get(chat_id)
    weekly_name = f"weekly:{chat_id}"
    for job in queue.get_jobs_by_name(weekly_name):
        job.schedule_removal()
    if subscriber and subscriber["enabled"] and (
        not config.allowed_user_ids or subscriber["user_id"] in config.allowed_user_ids
    ):
        hour, minute = map(int, subscriber["daily_time"].split(":"))
        queue.run_daily(
            send_daily_job,
            time(hour=hour, minute=minute, tzinfo=config.timezone),
            chat_id=chat_id,
            name=name,
            data=chat_id,
            job_kwargs={"misfire_grace_time": 3600, "coalesce": True, "max_instances": 1},
        )
        features = db.features(chat_id)
        if features["weekly_enabled"]:
            hour, minute = map(int, features["weekly_time"].split(":"))
            queue.run_daily(
                send_weekly_job,
                time(hour=hour, minute=minute, tzinfo=config.timezone),
                days=(0,),  # python-telegram-bot: domingo = 0
                chat_id=chat_id,
                name=weekly_name,
                data=chat_id,
                job_kwargs={"misfire_grace_time": 3600, "coalesce": True, "max_instances": 1},
            )


def message_for_items(items: list[NewsItem], config: Config, scheduled: bool) -> str:
    """Conserva la interfaz antigua; delega el formato trazable a summaries.py."""
    return format_news_message(items, config.timezone_name, scheduled=scheduled)


async def send_digest(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    topics: list[str],
    *,
    scheduled: bool = False,
    force_audio: bool = False,
) -> None:
    db = get_db(context)
    config = get_config(context)
    # Los resúmenes automáticos no repiten enlaces ya enviados anteriormente.
    result = await get_news(
        topics,
        limit=5,
        excluded_fingerprints=db.delivered_fingerprints(chat_id) if scheduled else None,
    )
    context.application.bot_data["rss_status"] = {
        "at": datetime.now(config.timezone).strftime("%d/%m/%Y %H:%M"),
        "feeds_ok": result.feeds_ok,
        "feeds_failed": result.feeds_failed,
    }
    if result.feeds_ok == 0:
        await context.bot.send_message(
            chat_id, "⚠️ No pude conectarme a las fuentes de noticias. "
                     "Revisa tu conexión a internet e inténtalo más tarde."
        )
        return
    items = result.items
    if not items:
        await context.bot.send_message(
            chat_id, "📭 No encontré novedades recientes para las categorías elegidas. "
                     "Prueba agregando temas con /temas o vuelve a consultar más tarde."
        )
        return
    content = message_for_items(items, config, scheduled)
    await context.bot.send_message(
        chat_id=chat_id,
        text=content,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=main_keyboard() if not scheduled else None,
    )
    db.record_news(chat_id, items)
    if scheduled:
        db.mark_delivered(chat_id, [item.fingerprint for item in items])
        db.mark_delivery(chat_id, "daily", datetime.now(config.timezone).date().isoformat())

    subscriber = db.get(chat_id)
    if force_audio or (subscriber and subscriber.get("voice_enabled")):
        try:
            audio = db.audio_settings(chat_id)
            await send_spoken_news(
                context, chat_id, items, scheduled=scheduled,
                voice_name=resolve_voice(audio["voice_key"], config.tts_voice),
                mode=audio["audio_mode"],
                rate=AUDIO_RATES[audio["rate_key"]][1],
            )
        except Exception:
            logger.exception("No se pudo generar o enviar el audio para %s", chat_id)
            await context.bot.send_message(
                chat_id,
                "⚠️ No pude generar el audio ahora. Ya tienes las noticias en texto. "
                "Comprueba la conexión a internet e inténtalo de nuevo con /escuchar.",
            )


async def send_daily_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.data
    db = get_db(context)
    subscriber = db.get(chat_id)
    if not subscriber or not subscriber["enabled"]:
        return
    try:
        # Se usa un solo envío por día (programado por el JobQueue).
        await send_digest(context, chat_id, db.topics(chat_id), scheduled=True)
        db.purge_old_deliveries()
    except Forbidden:
        logger.warning("El usuario %s bloqueó el bot. Se pausará la suscripción.", chat_id)
        db.set_enabled(chat_id, False)
        schedule_user(context.application, chat_id)
    except TelegramError:
        logger.exception("Error de Telegram enviando el resumen a %s", chat_id)
    except Exception:
        logger.exception("Error preparando el resumen para %s", chat_id)


async def send_weekly_preview(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """Envía un resumen del historial sin alterar la programación ni el control de duplicados."""
    db = get_db(context)
    config = get_config(context)
    topics = set(db.topics(chat_id))
    rows = [row for row in db.weekly_news(chat_id) if row["topic"] in topics]
    selected = choose_weekly(rows, limit=7)
    if len(selected) < 3:
        await context.bot.send_message(
            chat_id, "📭 Todavía no hay suficientes noticias guardadas para un resumen semanal "
                     "(mínimo 3). Se acumularán con los próximos envíos."
        )
        return
    await context.bot.send_message(
        chat_id=chat_id, text=format_weekly_message(selected, config.timezone_name),
        parse_mode=ParseMode.HTML, disable_web_page_preview=True,
    )
    return selected


async def send_weekly_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.data
    db = get_db(context)
    config = get_config(context)
    subscriber = db.get(chat_id)
    features = db.features(chat_id)
    if not subscriber or not subscriber["enabled"] or not features["weekly_enabled"]:
        return
    if config.allowed_user_ids and subscriber["user_id"] not in config.allowed_user_ids:
        return
    local_now = datetime.now(config.timezone)
    # Clave de la semana que termina este domingo.
    key = f"{local_now.isocalendar().year}-W{local_now.isocalendar().week:02d}"
    if not db.claim_delivery(chat_id, "weekly", key):
        return
    try:
        rows = await send_weekly_preview(context, chat_id)
        db.mark_delivery(chat_id, "weekly", key)
        if rows and subscriber.get("voice_enabled"):
            try:
                settings = db.audio_settings(chat_id)
                items = [
                    NewsItem(
                        row["topic"], row["title"], row["url"], row["source"],
                        datetime.fromisoformat(row["published_at"]),
                        row["description"], row["fingerprint"],
                    )
                    for row in rows[:5]
                ]
                await send_spoken_news(
                    context, chat_id, items, scheduled=False,
                    voice_name=resolve_voice(settings["voice_key"], config.tts_voice),
                    mode=settings["audio_mode"], rate=AUDIO_RATES[settings["rate_key"]][1],
                    intro="Este es tu resumen semanal de noticias.",
                )
            except Exception:
                logger.exception("No se pudo generar el audio semanal para %s", chat_id)
                await context.bot.send_message(
                    chat_id, "⚠️ No pude generar el audio semanal. Ya tienes el resumen en texto."
                )
    except Forbidden:
        db.release_delivery(chat_id, "weekly", key)
        db.set_enabled(chat_id, False)
        schedule_user(context.application, chat_id)
    except Exception:
        db.release_delivery(chat_id, "weekly", key)
        logger.exception("Error enviando resumen semanal a %s", chat_id)


async def send_alerts_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Una consulta RSS por ciclo, compartida entre suscriptores con alertas."""
    db = get_db(context)
    config = get_config(context)
    local_now = datetime.now(config.timezone)
    if in_quiet_hours(local_now):
        return
    subscribers = [
        sub for sub in db.subscribers()
        if sub["enabled"] and db.features(sub["chat_id"])["alerts_enabled"]
        and (not config.allowed_user_ids or sub["user_id"] in config.allowed_user_ids)
    ]
    if not subscribers:
        return
    topics = list(dict.fromkeys(topic for sub in subscribers for topic in db.topics(sub["chat_id"])))
    result = await get_news(topics, limit=50, max_age_hours=4)
    context.application.bot_data["rss_status"] = {
        "at": local_now.strftime("%d/%m/%Y %H:%M"),
        "feeds_ok": result.feeds_ok, "feeds_failed": result.feeds_failed,
    }
    if not result.feeds_ok:
        return
    midnight_utc = local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    for sub in subscribers:
        chat_id = sub["chat_id"]
        if db.sent_count_since(chat_id, "alert", midnight_utc) >= 2:
            continue
        item = choose_alert(result.items, db.topics(chat_id), db.delivered_fingerprints(chat_id), datetime.now(timezone.utc))
        if item is None or not db.claim_delivery(chat_id, "alert", item.fingerprint):
            continue
        try:
            await context.bot.send_message(
                chat_id=chat_id, text=alert_message(item),
                parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            )
            db.record_news(chat_id, [item])
            db.mark_delivered(chat_id, [item.fingerprint])
            db.mark_delivery(chat_id, "alert", item.fingerprint)
        except Forbidden:
            db.release_delivery(chat_id, "alert", item.fingerprint)
            db.set_feature(chat_id, "alerts_enabled", False)
            logger.warning("El usuario %s bloqueó el bot; alertas desactivadas.", chat_id)
        except Exception:
            db.release_delivery(chat_id, "alert", item.fingerprint)
            logger.exception("Error enviando alerta a %s", chat_id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    db = get_db(context)
    config = get_config(context)
    chat_id = update.effective_chat.id
    db.subscribe(chat_id, update.effective_user.id, config.default_time)
    schedule_user(context.application, chat_id)
    await update.message.reply_text(
        "👋 <b>¡Bienvenido a NewsBot!</b>\n\n"
        "Te envío un resumen con las noticias más recientes de los temas que te interesan. "
        "Tu horario inicial es <b>08:00 (Colombia)</b> si no has cambiado la configuración.\n\n"
        "🎯 Puedes escoger tus categorías, cambiar la hora y consultar noticias cuando quieras. "
        "Activa /voz para recibir también los resúmenes narrados. "
        "También puedes activar alertas destacadas y un resumen semanal desde el menú. "
        "Todo desde los botones de abajo.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    await update.message.reply_text(
        "<b>📚 Comandos de NewsBot</b>\n\n"
        "/start — comenzar y mostrar botones\n"
        "/noticias — noticias recientes de tus temas\n"
        "/voz — activar o desactivar audios automáticos\n"
        "/audio — elegir modo, voz y velocidad\n"
        "/alertas — configurar alertas destacadas\n"
        "/semanal — configurar resumen semanal\n"
        "/diagnostico — consultar estado del bot\n"
        "/escuchar — recibir noticias en audio ahora\n"
        "/categoria ia — consultar una categoría\n"
        "/temas — activar o desactivar categorías\n"
        "/hora 08:00 — cambiar hora diaria (24 h)\n"
        "/estado — revisar configuración\n"
        "/pausar — detener envíos diarios\n"
        "/reanudar — activar envíos diarios\n"
        "/fuentes — conocer el origen de las noticias\n"
        "/miid — consultar tu ID de Telegram\n"
        "/ayuda — mostrar esta lista\n\n"
        "<i>El bot debe permanecer ejecutándose en tu computador para enviar resúmenes automáticos.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


async def news_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    db = get_db(context)
    chat_id = update.effective_chat.id
    if db.get(chat_id) is None:
        await update.message.reply_text("Primero envía /start para iniciar tu suscripción.")
        return
    await update.message.reply_text("🔎 Buscando noticias recientes en tus categorías...")
    await send_digest(context, chat_id, db.topics(chat_id))


async def voice_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    db = get_db(context)
    chat_id = update.effective_chat.id
    subscriber = db.get(chat_id)
    if not subscriber:
        await update.message.reply_text("Primero envía /start para configurar el bot.")
        return
    enabled = not bool(subscriber.get("voice_enabled"))
    db.set_voice_enabled(chat_id, enabled)
    await update.message.reply_text(
        "🔊 Lectura en voz alta activada. Tus noticias llegarán en texto y audio."
        if enabled else "🔇 Lectura en voz alta desactivada. Seguirás recibiendo noticias en texto.",
        reply_markup=main_keyboard(),
    )


async def audio_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    db = get_db(context)
    chat_id = update.effective_chat.id
    subscriber = db.get(chat_id)
    if not subscriber:
        await update.message.reply_text("Primero envía /start para configurar el bot.")
        return
    await update.message.reply_text(
        audio_text(db.audio_settings(chat_id), bool(subscriber["voice_enabled"]), get_config(context).tts_voice),
        parse_mode=ParseMode.HTML,
        reply_markup=audio_keyboard(db.audio_settings(chat_id), bool(subscriber["voice_enabled"])),
    )


async def alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    db = get_db(context)
    chat_id = update.effective_chat.id
    if not db.get(chat_id):
        await update.message.reply_text("Primero envía /start.")
        return
    arg = context.args[0].lower() if context.args else ""
    if arg in ("on", "activar"):
        db.set_feature(chat_id, "alerts_enabled", True)
    elif arg in ("off", "desactivar"):
        db.set_feature(chat_id, "alerts_enabled", False)
    elif arg:
        await update.message.reply_text("Usa /alertas, /alertas on o /alertas off.")
        return
    enabled = bool(db.features(chat_id)["alerts_enabled"])
    await update.message.reply_text(
        alerts_text(enabled), parse_mode=ParseMode.HTML, reply_markup=alerts_keyboard(enabled)
    )


async def weekly_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    db = get_db(context)
    chat_id = update.effective_chat.id
    if not db.get(chat_id):
        await update.message.reply_text("Primero envía /start.")
        return
    arg = context.args[0].lower() if context.args else ""
    if arg in ("on", "activar"):
        db.set_feature(chat_id, "weekly_enabled", True)
    elif arg in ("off", "desactivar"):
        db.set_feature(chat_id, "weekly_enabled", False)
    elif arg in ("ver", "ahora"):
        await send_weekly_preview(context, chat_id)
        return
    elif arg:
        try:
            db.set_feature(chat_id, "weekly_time", validate_time(arg))
        except ValueError:
            await update.message.reply_text("Usa /semanal, /semanal on, /semanal off, /semanal ver o /semanal HH:MM.")
            return
    schedule_user(context.application, chat_id)
    settings = db.features(chat_id)
    await update.message.reply_text(
        weekly_text(bool(settings["weekly_enabled"]), settings["weekly_time"], get_config(context).timezone_name),
        parse_mode=ParseMode.HTML,
        reply_markup=weekly_keyboard(bool(settings["weekly_enabled"]), settings["weekly_time"]),
    )


async def diagnostic_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    db = get_db(context)
    await update.message.reply_text(
        diagnostic_text(db, update.effective_chat.id, context.application, get_config(context).timezone_name),
        parse_mode=ParseMode.HTML, reply_markup=main_keyboard(),
    )


async def listen_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    db = get_db(context)
    chat_id = update.effective_chat.id
    if not db.get(chat_id):
        await update.message.reply_text("Primero envía /start para configurar el bot.")
        return
    await update.message.reply_text("🎧 Preparando tus noticias narradas. Puede tardar unos segundos...")
    await send_digest(context, chat_id, db.topics(chat_id), force_audio=True)


async def category_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    topic = context.args[0].lower() if context.args else ""
    if topic not in TOPICS:
        await update.message.reply_text(
            "Usa /categoria seguido de: " + ", ".join(TOPICS) + "\nEjemplo: /categoria videojuegos"
        )
        return
    await update.message.reply_text("🔎 Consultando " + TOPICS[topic][1] + "...")
    await send_digest(context, update.effective_chat.id, [topic])


async def topics_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    db = get_db(context)
    chat_id = update.effective_chat.id
    if not db.get(chat_id):
        await update.message.reply_text("Primero envía /start.")
        return
    await update.message.reply_text(
        "🎯 <b>Selecciona tus temas:</b>\nToca una categoría para activarla o desactivarla. "
        "Las marcadas con ✅ están incluidas en tu resumen diario.",
        parse_mode=ParseMode.HTML,
        reply_markup=topics_keyboard(db.topics(chat_id)),
    )


async def time_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    db = get_db(context)
    chat_id = update.effective_chat.id
    if not db.get(chat_id):
        await update.message.reply_text("Primero envía /start.")
        return
    if not context.args:
        current = db.get(chat_id)["daily_time"]
        await update.message.reply_text(
            f"⏰ Envío actual: {current} ({get_config(context).timezone_name}).\n"
            "Selecciona una hora o escribe /hora 08:30.",
            reply_markup=times_keyboard(current),
        )
        return
    try:
        new_time = validate_time(context.args[0])
    except ValueError as exc:
        await update.message.reply_text(f"❌ {exc}")
        return
    db.set_time(chat_id, new_time)
    schedule_user(context.application, chat_id)
    await update.message.reply_text(
        f"✅ Enviaré tu resumen diario a las {new_time} ({get_config(context).timezone_name}).",
        reply_markup=main_keyboard(),
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    db = get_db(context)
    await update.message.reply_text(
        status_text(db, get_config(context), update.effective_chat.id),
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


async def set_enabled_command(update: Update, context: ContextTypes.DEFAULT_TYPE, enabled: bool) -> None:
    if not await check_access(update, context):
        return
    db = get_db(context)
    chat_id = update.effective_chat.id
    if not db.get(chat_id):
        await update.message.reply_text("Primero envía /start.")
        return
    db.set_enabled(chat_id, enabled)
    schedule_user(context.application, chat_id)
    await update.message.reply_text(
        "✅ Resúmenes diarios activados." if enabled else "⏸ Resúmenes diarios pausados.",
        reply_markup=main_keyboard(),
    )


async def pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await set_enabled_command(update, context, False)


async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await set_enabled_command(update, context, True)


async def sources_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    await update.message.reply_text(
        "📰 <b>Fuentes y funcionamiento</b>\n\n"
        "Este bot consulta <b>Google News RSS</b> en español para localizar titulares "
        "publicados por medios de comunicación. Incluye título, medio, enlace "
        "y, si existe, un fragmento del RSS.\n\n"
        "No genera artículos, no inventa resúmenes con IA, ni requiere claves de APIs de pago. "
        "La disponibilidad y selección dependen del RSS de Google News. "
        "Lee las notas originales para contrastar la información.",
        parse_mode=ParseMode.HTML,
    )


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.effective_message:
        return
    await update.effective_message.reply_text(
        f"🆔 Tu ID de Telegram es: <code>{update.effective_user.id}</code>",
        parse_mode=ParseMode.HTML,
    )


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    callback = update.callback_query
    await callback.answer()
    db = get_db(context)
    config = get_config(context)
    chat_id = update.effective_chat.id
    if not db.get(chat_id):
        await callback.message.reply_text("Primero envía /start para comenzar.")
        return
    data = callback.data or ""
    if data == "menu:home":
        await callback.edit_message_text("🏠 <b>Menú principal</b>", parse_mode=ParseMode.HTML, reply_markup=main_keyboard())
    elif data == "menu:topics" or data.startswith("topic:"):
        if data.startswith("topic:"):
            _, topic = data.split(":", 1)
            ok, message = db.toggle_topic(chat_id, topic)
            if not ok:
                await callback.message.reply_text("⚠️ " + message)
        await callback.edit_message_text(
            "🎯 <b>Elige tus temas</b>\nPulsa para activar o desactivar categorías. "
            "Debes mantener al menos una.",
            parse_mode=ParseMode.HTML,
            reply_markup=topics_keyboard(db.topics(chat_id)),
        )
    elif data == "menu:time" or data.startswith("time:"):
        if data.startswith("time:"):
            value = data.split(":", 1)[1]
            try:
                value = validate_time(value)
            except ValueError:
                await callback.message.reply_text("Hora inválida.")
                return
            db.set_time(chat_id, value)
            schedule_user(context.application, chat_id)
        current = db.get(chat_id)["daily_time"]
        await callback.edit_message_text(
            f"⏰ <b>Hora del resumen: {current}</b>\n"
            f"Zona horaria: {escape(config.timezone_name)}\n"
            "Elige otra hora o envía /hora HH:MM.",
            parse_mode=ParseMode.HTML,
            reply_markup=times_keyboard(current),
        )
    elif data == "menu:voice":
        # Compatibilidad con botones de mensajes anteriores.
        enabled = not bool(db.get(chat_id).get("voice_enabled"))
        db.set_voice_enabled(chat_id, enabled)
        await callback.edit_message_text(
            audio_text(db.audio_settings(chat_id), enabled, config.tts_voice),
            parse_mode=ParseMode.HTML,
            reply_markup=audio_keyboard(db.audio_settings(chat_id), enabled),
        )
    elif data == "menu:audio" or data.startswith("audio:"):
        if data == "audio:toggle":
            db.set_voice_enabled(chat_id, not bool(db.get(chat_id)["voice_enabled"]))
        elif data.startswith("audio:"):
            parts = data.split(":")
            columns = {"mode": ("audio_mode", AUDIO_MODES),
                       "voice": ("voice_key", AUDIO_VOICES),
                       "rate": ("rate_key", AUDIO_RATES)}
            if len(parts) != 3 or parts[1] not in columns or parts[2] not in columns[parts[1]][1]:
                await callback.message.reply_text("⚠️ Opción de audio no válida.")
                return
            field = columns[parts[1]][0]
            if db.audio_settings(chat_id)[field] == parts[2]:
                return  # Evita el error Telegram 'Message is not modified'.
            db.set_audio_setting(chat_id, field, parts[2])
        enabled = bool(db.get(chat_id)["voice_enabled"])
        settings = db.audio_settings(chat_id)
        await callback.edit_message_text(
            audio_text(settings, enabled, config.tts_voice),
            parse_mode=ParseMode.HTML,
            reply_markup=audio_keyboard(settings, enabled),
        )
    elif data == "menu:alerts" or data.startswith("alerts:"):
        if data == "alerts:toggle":
            db.set_feature(chat_id, "alerts_enabled", not bool(db.features(chat_id)["alerts_enabled"]))
        enabled = bool(db.features(chat_id)["alerts_enabled"])
        await callback.edit_message_text(
            alerts_text(enabled), parse_mode=ParseMode.HTML, reply_markup=alerts_keyboard(enabled),
        )
    elif data == "menu:weekly" or data.startswith("weekly:"):
        if data == "weekly:toggle":
            db.set_feature(chat_id, "weekly_enabled", not bool(db.features(chat_id)["weekly_enabled"]))
            schedule_user(context.application, chat_id)
        elif data.startswith("weekly:time:"):
            try:
                db.set_feature(chat_id, "weekly_time", validate_time(data.split(":", 2)[2]))
            except ValueError:
                await callback.message.reply_text("Hora semanal inválida.")
                return
            schedule_user(context.application, chat_id)
        elif data == "weekly:preview":
            await send_weekly_preview(context, chat_id)
            return
        settings = db.features(chat_id)
        await callback.edit_message_text(
            weekly_text(bool(settings["weekly_enabled"]), settings["weekly_time"], config.timezone_name),
            parse_mode=ParseMode.HTML,
            reply_markup=weekly_keyboard(bool(settings["weekly_enabled"]), settings["weekly_time"]),
        )
    elif data == "menu:diagnostics":
        await callback.edit_message_text(
            diagnostic_text(db, chat_id, context.application, config.timezone_name),
            parse_mode=ParseMode.HTML, reply_markup=main_keyboard(),
        )
    elif data == "menu:status":
        await callback.edit_message_text(
            status_text(db, config, chat_id),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
        )
    elif data == "menu:toggle":
        enabled = not bool(db.get(chat_id)["enabled"])
        db.set_enabled(chat_id, enabled)
        schedule_user(context.application, chat_id)
        await callback.edit_message_text(
            "✅ Envíos diarios activados." if enabled else "⏸ Envíos diarios pausados.",
            reply_markup=main_keyboard(),
        )
    elif data == "news:all":
        await callback.message.reply_text("🔎 Buscando noticias recientes...")
        await send_digest(context, chat_id, db.topics(chat_id))
    elif data == "news:listen":
        await callback.message.reply_text("🎧 Preparando las noticias narradas...")
        await send_digest(context, chat_id, db.topics(chat_id), force_audio=True)


async def post_init(application: Application) -> None:
    db: Database = application.bot_data["db"]
    await application.bot.set_my_commands([
        BotCommand("start", "Iniciar y abrir el menú"),
        BotCommand("noticias", "Leer noticias recientes"),
        BotCommand("voz", "Activar o desactivar narración"),
        BotCommand("audio", "Configurar voz, modo y velocidad"),
        BotCommand("alertas", "Alertas de novedades destacadas"),
        BotCommand("semanal", "Resumen de los últimos 7 días"),
        BotCommand("diagnostico", "Diagnóstico del bot"),
        BotCommand("escuchar", "Escuchar noticias en audio"),
        BotCommand("temas", "Elegir temas"),
        BotCommand("hora", "Cambiar la hora diaria"),
        BotCommand("estado", "Ver mi configuración"),
        BotCommand("pausar", "Pausar mensajes diarios"),
        BotCommand("reanudar", "Reactivar mensajes diarios"),
        BotCommand("ayuda", "Ayuda y comandos"),
    ])
    for subscriber in db.subscribers():
        schedule_user(application, subscriber["chat_id"])
    application.job_queue.run_repeating(
        send_alerts_job, interval=1800, first=180, name="alerts:scan",
        job_kwargs={"coalesce": True, "max_instances": 1},
    )
    logger.info("Cargadas %d suscripciones de SQLite", len(db.subscribers()))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Error gestionando una actualización: %s", context.error, exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                update.effective_chat.id,
                "⚠️ Ocurrió un problema al procesar tu solicitud. Inténtalo nuevamente.",
            )
        except TelegramError:
            pass


def create_application(config: Config, database: Database) -> Application:
    app = Application.builder().token(config.token).post_init(post_init).build()
    app.bot_data["config"] = config
    app.bot_data["db"] = database
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler(["ayuda", "help"], help_command))
    app.add_handler(CommandHandler(["noticias", "ahora"], news_command))
    app.add_handler(CommandHandler("voz", voice_command))
    app.add_handler(CommandHandler("audio", audio_command))
    app.add_handler(CommandHandler("alertas", alerts_command))
    app.add_handler(CommandHandler("semanal", weekly_command))
    app.add_handler(CommandHandler("diagnostico", diagnostic_command))
    app.add_handler(CommandHandler("escuchar", listen_command))
    app.add_handler(CommandHandler("categoria", category_command))
    app.add_handler(CommandHandler("temas", topics_command))
    app.add_handler(CommandHandler("hora", time_command))
    app.add_handler(CommandHandler("estado", status_command))
    app.add_handler(CommandHandler("pausar", pause_command))
    app.add_handler(CommandHandler("reanudar", resume_command))
    app.add_handler(CommandHandler("fuentes", sources_command))
    app.add_handler(CommandHandler("miid", id_command))
    app.add_handler(CallbackQueryHandler(callbacks, pattern=r"^(?:menu:|topic:|time:|news:|audio:|alerts:|weekly:).+"))
    app.add_error_handler(error_handler)
    return app

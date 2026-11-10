"""Comandos y botones del bot de noticias Telegram."""

from __future__ import annotations

from datetime import datetime, time
from html import escape
import logging

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.error import Forbidden, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from .config import Config, validate_time
from .database import Database
from .news import TOPICS, NewsItem, get_news
from .voice import send_spoken_news

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
        [InlineKeyboardButton("🔊 Activar / desactivar voz", callback_data="menu:voice"),
         InlineKeyboardButton("🎧 Escuchar noticias", callback_data="news:listen")],
        [InlineKeyboardButton("📋 Mi configuración", callback_data="menu:status"),
         InlineKeyboardButton("⏯ Pausar / activar", callback_data="menu:toggle")],
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


def format_topics(active: list[str]) -> str:
    return ", ".join(f"{TOPICS[t][0]} {TOPICS[t][1]}" for t in active) or "Ninguna"


def status_text(db: Database, config: Config, chat_id: int) -> str:
    subscriber = db.get(chat_id)
    if not subscriber:
        return "Primero envía /start para configurar tu resumen."
    status = "🟢 Activado" if subscriber["enabled"] else "⏸ Pausado"
    return (
        "<b>⚙️ Tu configuración</b>\n\n"
        f"Estado: {status}\n"
        f"Lectura en voz alta: {'🔊 Activada' if subscriber.get('voice_enabled') else '🔇 Desactivada'}\n"
        f"Envío diario: <b>{subscriber['daily_time']}</b>\n"
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
    if subscriber and subscriber["enabled"]:
        hour, minute = map(int, subscriber["daily_time"].split(":"))
        queue.run_daily(
            send_daily_job,
            time(hour=hour, minute=minute, tzinfo=config.timezone),
            chat_id=chat_id,
            name=name,
            data=chat_id,
            job_kwargs={"misfire_grace_time": 3600, "coalesce": True, "max_instances": 1},
        )


def message_for_items(items: list[NewsItem], config: Config, scheduled: bool) -> str:
    date_label = datetime.now(config.timezone).strftime("%d/%m/%Y")
    headline = "☀️ <b>Tu resumen diario</b>" if scheduled else "🗞 <b>Noticias para ti</b>"
    lines = [f"{headline} · {date_label}", f"<i>{len(items)} noticias recientes • Zona: {escape(config.timezone_name)}</i>"]
    for i, item in enumerate(items, 1):
        emoji, label, _ = TOPICS[item.topic]
        lines.append(f"\n<b>{i}. {escape(item.title)}</b>")
        lines.append(f"{emoji} {escape(label)} · {escape(item.source)}")
        if item.description:
            lines.append(escape(item.description))
        # URL obtenida del RSS y validada como HTTPS. Nunca se forma con texto de usuarios.
        lines.append(f'<a href="{escape(item.url, quote=True)}">🔗 Leer noticia completa</a>')
    lines.append("\n<i>Fuente: titulares y fragmentos de Google News RSS. Abre los enlaces para verificar detalles.</i>")
    return "\n".join(lines)


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
    if scheduled:
        db.mark_delivered(chat_id, [item.fingerprint for item in items])

    subscriber = db.get(chat_id)
    if force_audio or (subscriber and subscriber.get("voice_enabled")):
        try:
            await send_spoken_news(
                context, chat_id, items, scheduled=scheduled, voice_name=config.tts_voice
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
        enabled = not bool(db.get(chat_id).get("voice_enabled"))
        db.set_voice_enabled(chat_id, enabled)
        await callback.edit_message_text(
            "🔊 Lectura en voz alta activada: recibirás noticias en texto y audio."
            if enabled else "🔇 Lectura en voz alta desactivada: solo recibirás texto.",
            reply_markup=main_keyboard(),
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
    app.add_handler(CommandHandler("escuchar", listen_command))
    app.add_handler(CommandHandler("categoria", category_command))
    app.add_handler(CommandHandler("temas", topics_command))
    app.add_handler(CommandHandler("hora", time_command))
    app.add_handler(CommandHandler("estado", status_command))
    app.add_handler(CommandHandler("pausar", pause_command))
    app.add_handler(CommandHandler("reanudar", resume_command))
    app.add_handler(CommandHandler("fuentes", sources_command))
    app.add_handler(CommandHandler("miid", id_command))
    app.add_handler(CallbackQueryHandler(callbacks, pattern=r"^(?:menu:|topic:|time:|news:).+"))
    app.add_error_handler(error_handler)
    return app

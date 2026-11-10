"""Preferencias seguras y opciones de narración disponibles por suscriptor."""

from __future__ import annotations

from html import escape

AUDIO_MODES = {
    "rapido": ("⚡ Rápido", "hasta aproximadamente 1 minuto"),
    "normal": ("🎙 Normal", "hasta aproximadamente 3 minutos"),
    "detallado": ("📖 Detallado", "hasta aproximadamente 5 minutos"),
}

AUDIO_VOICES = {
    "predeterminada": ("⚙️ Predeterminada", None),
    "salome": ("👩 Salomé", "es-CO-SalomeNeural"),
    "gonzalo": ("👨 Gonzalo", "es-CO-GonzaloNeural"),
}

AUDIO_RATES = {
    "lenta": ("🐢 Lenta", "-15%"),
    "normal": ("▶️ Normal", "+0%"),
    "rapida": ("🚀 Rápida", "+15%"),
}

DEFAULT_AUDIO_SETTINGS = {
    "audio_mode": "normal",
    "voice_key": "predeterminada",
    "rate_key": "normal",
}


def resolve_voice(key: str, default_voice: str) -> str:
    """Usa la voz global configurada cuando el usuario no eligió una propia."""
    if key not in AUDIO_VOICES:
        raise ValueError("Voz desconocida")
    return AUDIO_VOICES[key][1] or default_voice


def audio_text(settings: dict, enabled: bool, default_voice: str) -> str:
    mode = AUDIO_MODES[settings["audio_mode"]]
    voice = AUDIO_VOICES[settings["voice_key"]]
    rate = AUDIO_RATES[settings["rate_key"]]
    actual_voice = resolve_voice(settings["voice_key"], default_voice)
    return (
        "<b>🎧 Audio y voces</b>\n\n"
        f"Narración diaria: {'🔊 Activada' if enabled else '🔇 Desactivada'}\n"
        f"Modo: <b>{escape(mode[0])}</b> ({escape(mode[1])})\n"
        f"Voz: <b>{escape(voice[0])}</b> ({escape(actual_voice)})\n"
        f"Velocidad: <b>{escape(rate[0])}</b>\n\n"
        "Elige las opciones con los botones. Las duraciones son máximos aproximados: "
        "si las fuentes aportan pocos datos, el audio será más corto. "
        "Puedes usar /escuchar para probarlo ahora."
    )


def audio_keyboard(settings: dict, enabled: bool) -> InlineKeyboardMarkup:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    def option(label: str, selected: bool, data: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(("✅ " if selected else "") + label, callback_data=data)

    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔊 Desactivar audio diario" if enabled else "🔇 Activar audio diario", callback_data="audio:toggle")],
        [option(label, settings["audio_mode"] == key, f"audio:mode:{key}") for key, (label, _) in AUDIO_MODES.items()],
        [option(label, settings["voice_key"] == key, f"audio:voice:{key}") for key, (label, _) in AUDIO_VOICES.items()],
        [option(label, settings["rate_key"] == key, f"audio:rate:{key}") for key, (label, _) in AUDIO_RATES.items()],
        [InlineKeyboardButton("🎧 Probar narración", callback_data="news:listen")],
        [InlineKeyboardButton("🏠 Volver al menú", callback_data="menu:home")],
    ])

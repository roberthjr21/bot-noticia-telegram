"""Narración opcional en español con modos, velocidad y voz por usuario.

La voz se sintetiza en línea mediante edge-tts. Los audios son temporales.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from .audio_settings import AUDIO_MODES, AUDIO_RATES
from .news import NewsItem, TOPICS, clean_text

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

log = logging.getLogger(__name__)


def speech_text(items: list[NewsItem], *, scheduled: bool = False, mode: str = "normal") -> str:
    """Construye guion a partir de titulares y RSS; no inventa detalles ni lee URLs."""
    if mode not in AUDIO_MODES:
        raise ValueError("Modo de audio desconocido")
    opening = (
        "Buenos días. Aquí tienes tu resumen diario de noticias."
        if scheduled else "Estas son las noticias más recientes para ti."
    )
    lines = [opening]
    selected = items[:3] if mode == "rapido" else items[:5]
    for index, item in enumerate(selected, 1):
        label = TOPICS.get(item.topic, ("", "Actualidad", ""))[1]
        title = clean_text(item.title, 240)
        description = clean_text(item.description, 300 if mode == "detallado" else 180)
        lines.append(f"Noticia {index}. {label}. {title}.")
        if mode == "detallado":
            source = clean_text(item.source, 70)
            if source:
                lines.append(f"Medio: {source}.")
            if item.published:
                lines.append(f"Fecha de publicación: {item.published:%d/%m/%Y}.")
        if mode != "rapido" and description and description.lower() not in title.lower():
            lines.append(description + ".")
    lines.append("Puedes abrir los enlaces del mensaje para conocer todos los detalles.")
    max_chars = {"rapido": 700, "normal": 1900, "detallado": 3500}[mode]
    script = " ".join(lines)
    if len(script) > max_chars:
        script = script[:max_chars].rsplit(" ", 1)[0].rstrip(" ,.;:") + "."
    return script


async def send_spoken_news(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    items: list[NewsItem],
    *,
    scheduled: bool,
    voice_name: str,
    mode: str = "normal",
    rate: str = "+0%",
) -> None:
    """Genera y envía audio; los fallos se manejan en el llamador."""
    if rate not in {value[1] for value in AUDIO_RATES.values()}:
        raise ValueError("Velocidad de audio desconocida")
    import edge_tts  # Import diferido; las pruebas no requieren internet.

    script = speech_text(items, scheduled=scheduled, mode=mode)
    with TemporaryDirectory(prefix="newsbot_audio_") as temp:
        mp3 = Path(temp) / "noticias.mp3"
        await asyncio.wait_for(
            edge_tts.Communicate(script, voice=voice_name, rate=rate).save(str(mp3)), timeout=100
        )
        if not mp3.exists() or mp3.stat().st_size == 0:
            raise RuntimeError("El servicio de voz no produjo un audio válido")

        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            ogg = Path(temp) / "noticias.ogg"
            proc = await asyncio.create_subprocess_exec(
                ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(mp3), "-c:a", "libopus", "-b:a", "32k", str(ogg),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                raise RuntimeError("La conversión de audio tardó demasiado")
            if proc.returncode == 0 and ogg.exists() and ogg.stat().st_size:
                with ogg.open("rb") as audio:
                    await context.bot.send_voice(chat_id=chat_id, voice=audio, caption="🔊 Noticias narradas")
                return
            log.warning("No se pudo convertir a OGG; se enviará MP3: %s", stderr.decode(errors="replace")[:250])

        with mp3.open("rb") as audio:
            await context.bot.send_audio(chat_id=chat_id, audio=audio, title="Noticias narradas", performer="NewsBot")

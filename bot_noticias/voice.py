"""Lectura opcional de noticias en español mediante síntesis de voz en línea.

Si ffmpeg está instalado, Telegram recibe una nota de voz OGG/Opus.
Sin ffmpeg, recibe un archivo MP3 reproducible. Nunca guarda audios permanentemente.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

from telegram.ext import ContextTypes

from .news import NewsItem, TOPICS, clean_text

log = logging.getLogger(__name__)


def speech_text(items: list[NewsItem], *, scheduled: bool = False) -> str:
    """Crea un guion breve; no lee URLs ni texto HTML del RSS."""
    opening = (
        "Buenos días. Aquí tienes tu resumen diario de noticias."
        if scheduled else "Estas son las noticias más recientes para ti."
    )
    lines = [opening]
    for index, item in enumerate(items[:5], 1):
        label = TOPICS.get(item.topic, ("", "Actualidad", ""))[1]
        title = clean_text(item.title, 240)
        description = clean_text(item.description, 180)
        lines.append(f"Noticia {index}. {label}. {title}.")
        if description and description.lower() not in title.lower():
            lines.append(description + ".")
    lines.append("Puedes abrir los enlaces del mensaje para conocer todos los detalles.")
    return " ".join(lines)[:3500]


async def send_spoken_news(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    items: list[NewsItem],
    *,
    scheduled: bool,
    voice_name: str,
) -> None:
    """Genera y envía el audio; falla sin interrumpir la entrega del texto."""
    import edge_tts  # Import diferido para facilitar las pruebas sin internet.

    script = speech_text(items, scheduled=scheduled)
    with TemporaryDirectory(prefix="newsbot_audio_") as temp:
        mp3 = Path(temp) / "noticias.mp3"
        await asyncio.wait_for(edge_tts.Communicate(script, voice=voice_name).save(str(mp3)), timeout=100)
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

"""Configuración cargada desde variables de entorno, nunca desde secretos en Git."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import os

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    token: str
    timezone: ZoneInfo
    timezone_name: str
    default_time: str
    database_path: Path
    allowed_user_ids: frozenset[int]
    tts_voice: str = "es-CO-SalomeNeural"


def validate_time(value: str) -> str:
    """Valida HH:MM en formato de 24 horas, sin segundos."""
    if len(value) != 5 or value[2] != ":":
        raise ValueError("Usa HH:MM, por ejemplo 08:00")
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise ValueError("Hora inválida. Usa HH:MM (00:00 a 23:59).") from exc
    return parsed.strftime("%H:%M")


def load_config() -> Config:
    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or token == "PON_AQUI_TU_TOKEN":
        raise ValueError("Falta TELEGRAM_BOT_TOKEN. Copia .env.example a .env y agrega el token de @BotFather.")

    tz_name = os.getenv("BOT_TZ", "America/Bogota").strip()
    try:
        timezone = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Zona horaria no encontrada: {tz_name}. Instala tzdata si estás en Windows.") from exc

    default_time = validate_time(os.getenv("BOT_DAILY_TIME", "08:00").strip())
    raw_ids = os.getenv("ALLOWED_USER_IDS", "").strip()
    try:
        allowed = frozenset(int(part.strip()) for part in raw_ids.split(",") if part.strip())
    except ValueError as exc:
        raise ValueError("ALLOWED_USER_IDS debe contener IDs numéricos separados por coma.") from exc

    root = Path(__file__).resolve().parent.parent
    data_dir = Path(os.getenv("DATA_DIR", "data"))
    if not data_dir.is_absolute():
        data_dir = root / data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    tts_voice = os.getenv("TTS_VOICE", "es-CO-SalomeNeural").strip() or "es-CO-SalomeNeural"
    return Config(token, timezone, tz_name, default_time, data_dir / "news_bot.sqlite3", allowed, tts_voice)

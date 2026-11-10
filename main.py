"""Ejecuta el bot usando long polling (sin servidor web ni webhook)."""

import logging
import sys

from telegram import Update

from bot_noticias.config import load_config
from bot_noticias.database import Database
from bot_noticias.bot import create_application


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        config = load_config()
        db = Database(config.database_path)
        app = create_application(config, db)
    except (ValueError, RuntimeError) as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return 1
    print(f"NewsBot iniciado. Zona horaria: {config.timezone_name}. Presiona Ctrl+C para detener.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Persistencia local SQLite para suscripciones, temas y noticias ya enviadas."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator
from pathlib import Path
import sqlite3

from .news import DEFAULT_TOPICS, TOPICS
from .audio_settings import AUDIO_MODES, AUDIO_VOICES, AUDIO_RATES, DEFAULT_AUDIO_SETTINGS


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS subscribers (
                    chat_id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    daily_time TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                    voice_enabled INTEGER NOT NULL DEFAULT 0 CHECK (voice_enabled IN (0, 1)),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS preferences (
                    chat_id INTEGER NOT NULL,
                    topic TEXT NOT NULL,
                    PRIMARY KEY(chat_id, topic),
                    FOREIGN KEY(chat_id) REFERENCES subscribers(chat_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS audio_preferences (
                    chat_id INTEGER PRIMARY KEY,
                    audio_mode TEXT NOT NULL DEFAULT 'normal'
                        CHECK (audio_mode IN ('rapido', 'normal', 'detallado')),
                    voice_key TEXT NOT NULL DEFAULT 'predeterminada'
                        CHECK (voice_key IN ('predeterminada', 'salome', 'gonzalo')),
                    rate_key TEXT NOT NULL DEFAULT 'normal'
                        CHECK (rate_key IN ('lenta', 'normal', 'rapida')),
                    FOREIGN KEY(chat_id) REFERENCES subscribers(chat_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS delivered (
                    chat_id INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    sent_at TEXT NOT NULL,
                    PRIMARY KEY(chat_id, fingerprint),
                    FOREIGN KEY(chat_id) REFERENCES subscribers(chat_id) ON DELETE CASCADE
                );
            """)
            # Migración no destructiva: conserva las suscripciones existentes.
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(subscribers)")}
            if "voice_enabled" not in columns:
                conn.execute(
                    "ALTER TABLE subscribers ADD COLUMN voice_enabled INTEGER NOT NULL DEFAULT 0 "
                    "CHECK (voice_enabled IN (0, 1))"
                )

    def subscribe(self, chat_id: int, user_id: int, default_time: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO subscribers(chat_id, user_id, daily_time) VALUES (?, ?, ?)",
                (chat_id, user_id, default_time),
            )
            has_preferences = conn.execute(
                "SELECT 1 FROM preferences WHERE chat_id = ? LIMIT 1", (chat_id,)
            ).fetchone()
            if not has_preferences:
                conn.executemany(
                    "INSERT INTO preferences(chat_id, topic) VALUES (?, ?)",
                    [(chat_id, topic) for topic in DEFAULT_TOPICS],
                )
            conn.execute("INSERT OR IGNORE INTO audio_preferences(chat_id) VALUES (?)", (chat_id,))
            # Para usuarios existentes, preserva los temas que eligieron.

    def subscribers(self) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM subscribers")]

    def get(self, chat_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM subscribers WHERE chat_id = ?", (chat_id,)).fetchone()
            return dict(row) if row else None

    def topics(self, chat_id: int) -> list[str]:
        with self.connect() as conn:
            found = {row["topic"] for row in conn.execute(
                "SELECT topic FROM preferences WHERE chat_id = ?", (chat_id,)
            )}
        return [topic for topic in TOPICS if topic in found]

    def toggle_topic(self, chat_id: int, topic: str) -> tuple[bool, str]:
        if topic not in TOPICS:
            return False, "Esa categoría no existe."
        with self.connect() as conn:
            active = conn.execute(
                "SELECT 1 FROM preferences WHERE chat_id = ? AND topic = ?", (chat_id, topic)
            ).fetchone()
            if active:
                count = conn.execute(
                    "SELECT COUNT(*) FROM preferences WHERE chat_id = ?", (chat_id,)
                ).fetchone()[0]
                if count <= 1:
                    return False, "Debes mantener al menos una categoría activada."
                conn.execute("DELETE FROM preferences WHERE chat_id = ? AND topic = ?", (chat_id, topic))
            else:
                conn.execute(
                    "INSERT INTO preferences(chat_id, topic) VALUES (?, ?)", (chat_id, topic)
                )
        return True, "Categorías actualizadas."

    def set_time(self, chat_id: int, daily_time: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE subscribers SET daily_time = ? WHERE chat_id = ?", (daily_time, chat_id))

    def set_enabled(self, chat_id: int, enabled: bool) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE subscribers SET enabled = ? WHERE chat_id = ?", (int(enabled), chat_id))

    def set_voice_enabled(self, chat_id: int, enabled: bool) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE subscribers SET voice_enabled = ? WHERE chat_id = ?", (int(enabled), chat_id))

    def audio_settings(self, chat_id: int) -> dict | None:
        """Crea valores por defecto para suscriptores antiguos sin perder datos."""
        with self.connect() as conn:
            if not conn.execute("SELECT 1 FROM subscribers WHERE chat_id = ?", (chat_id,)).fetchone():
                return None
            conn.execute("INSERT OR IGNORE INTO audio_preferences(chat_id) VALUES (?)", (chat_id,))
            row = conn.execute(
                "SELECT audio_mode, voice_key, rate_key FROM audio_preferences WHERE chat_id = ?", (chat_id,)
            ).fetchone()
            return dict(row) if row else DEFAULT_AUDIO_SETTINGS.copy()

    def set_audio_setting(self, chat_id: int, setting: str, value: str) -> None:
        allowed = {"audio_mode": AUDIO_MODES, "voice_key": AUDIO_VOICES, "rate_key": AUDIO_RATES}
        if setting not in allowed or value not in allowed[setting]:
            raise ValueError("Preferencia de audio inválida")
        with self.connect() as conn:
            if not conn.execute("SELECT 1 FROM subscribers WHERE chat_id = ?", (chat_id,)).fetchone():
                raise ValueError("Suscriptor no registrado")
            conn.execute("INSERT OR IGNORE INTO audio_preferences(chat_id) VALUES (?)", (chat_id,))
            conn.execute(f"UPDATE audio_preferences SET {setting} = ? WHERE chat_id = ?", (value, chat_id))

    def was_delivered(self, chat_id: int, fingerprint: str) -> bool:
        with self.connect() as conn:
            return conn.execute(
                "SELECT 1 FROM delivered WHERE chat_id = ? AND fingerprint = ?", (chat_id, fingerprint)
            ).fetchone() is not None

    def delivered_fingerprints(self, chat_id: int) -> set[str]:
        with self.connect() as conn:
            return {
                row["fingerprint"] for row in conn.execute(
                    "SELECT fingerprint FROM delivered WHERE chat_id = ?", (chat_id,)
                )
            }

    def mark_delivered(self, chat_id: int, fingerprints: list[str]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO delivered(chat_id, fingerprint, sent_at) VALUES (?, ?, ?)",
                [(chat_id, fingerprint, now) for fingerprint in fingerprints],
            )

    def purge_old_deliveries(self, days: int = 45) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.connect() as conn:
            conn.execute("DELETE FROM delivered WHERE sent_at < ?", (cutoff,))

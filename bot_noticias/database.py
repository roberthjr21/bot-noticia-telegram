"""Persistencia local SQLite para suscripciones, temas y noticias ya enviadas."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator, Iterable
from pathlib import Path
import sqlite3

from .news import DEFAULT_TOPICS, TOPICS
from .audio_settings import AUDIO_MODES, AUDIO_VOICES, AUDIO_RATES, DEFAULT_AUDIO_SETTINGS
from .config import validate_time
from .news import NewsItem


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
                CREATE TABLE IF NOT EXISTS subscriber_features (
                    chat_id INTEGER PRIMARY KEY,
                    weekly_enabled INTEGER NOT NULL DEFAULT 0 CHECK (weekly_enabled IN (0, 1)),
                    weekly_time TEXT NOT NULL DEFAULT '18:00',
                    alerts_enabled INTEGER NOT NULL DEFAULT 0 CHECK (alerts_enabled IN (0, 1)),
                    FOREIGN KEY(chat_id) REFERENCES subscribers(chat_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS news_history (
                    chat_id INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    source TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    first_seen TEXT NOT NULL,
                    PRIMARY KEY (chat_id, fingerprint),
                    FOREIGN KEY(chat_id) REFERENCES subscribers(chat_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_news_history_time ON news_history(chat_id, first_seen);
                CREATE TABLE IF NOT EXISTS delivery_events (
                    chat_id INTEGER NOT NULL,
                    kind TEXT NOT NULL CHECK (kind IN ('daily', 'weekly', 'alert')),
                    delivery_key TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'sent')),
                    created_at TEXT NOT NULL,
                    sent_at TEXT,
                    PRIMARY KEY(chat_id, kind, delivery_key),
                    FOREIGN KEY(chat_id) REFERENCES subscribers(chat_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_delivery_events_time ON delivery_events(chat_id, kind, status, sent_at);
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
            conn.execute("INSERT OR IGNORE INTO subscriber_features(chat_id) VALUES (?)", (chat_id,))
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

    def features(self, chat_id: int) -> dict | None:
        """Preferencias opt-in; migra los suscriptores antiguos al primer acceso."""
        with self.connect() as conn:
            if not conn.execute("SELECT 1 FROM subscribers WHERE chat_id = ?", (chat_id,)).fetchone():
                return None
            conn.execute("INSERT OR IGNORE INTO subscriber_features(chat_id) VALUES (?)", (chat_id,))
            row = conn.execute(
                "SELECT weekly_enabled, weekly_time, alerts_enabled "
                "FROM subscriber_features WHERE chat_id = ?", (chat_id,)
            ).fetchone()
            return dict(row)

    def set_feature(self, chat_id: int, name: str, value: bool | str) -> None:
        if name not in ("weekly_enabled", "weekly_time", "alerts_enabled"):
            raise ValueError("Preferencia desconocida")
        if name == "weekly_time":
            value = validate_time(str(value))
        elif not isinstance(value, bool):
            raise ValueError("Se requiere un valor booleano")
        else:
            value = int(value)
        with self.connect() as conn:
            if not conn.execute("SELECT 1 FROM subscribers WHERE chat_id = ?", (chat_id,)).fetchone():
                raise ValueError("Suscriptor inexistente")
            conn.execute("INSERT OR IGNORE INTO subscriber_features(chat_id) VALUES (?)", (chat_id,))
            conn.execute(f"UPDATE subscriber_features SET {name} = ? WHERE chat_id = ?", (value, chat_id))

    def record_news(self, chat_id: int, items: Iterable[NewsItem]) -> None:
        """Conserva únicamente artículos que se hayan entregado correctamente al chat."""
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO news_history"
                "(chat_id, fingerprint, topic, title, url, source, published_at, description, first_seen)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (chat_id, item.fingerprint, item.topic, item.title, item.url,
                     item.source, item.published.astimezone(timezone.utc).isoformat(),
                     item.description, now)
                    for item in items if item.published is not None
                ],
            )

    def weekly_news(self, chat_id: int, days: int = 7) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT topic, title, url, source, published_at, description, fingerprint "
                "FROM news_history WHERE chat_id = ? AND first_seen >= ? "
                "ORDER BY published_at DESC LIMIT 150", (chat_id, cutoff)
            )]

    def news_count(self, chat_id: int, days: int = 7) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM news_history WHERE chat_id = ? AND first_seen >= ?",
                (chat_id, cutoff),
            ).fetchone()[0]

    def claim_delivery(self, chat_id: int, kind: str, key: str) -> bool:
        """Reserva una entrega, evitando duplicaciones por ejecución concurrente."""
        if kind not in ("daily", "weekly", "alert"):
            raise ValueError("Tipo de entrega desconocido")
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(hours=2)).isoformat()
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM delivery_events WHERE chat_id = ? AND kind = ? "
                "AND delivery_key = ? AND status = 'pending' AND created_at < ?",
                (chat_id, kind, key, cutoff),
            )
            cursor = conn.execute(
                "INSERT OR IGNORE INTO delivery_events"
                "(chat_id, kind, delivery_key, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
                (chat_id, kind, key, now.isoformat()),
            )
            return cursor.rowcount == 1

    def mark_delivery(self, chat_id: int, kind: str, key: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO delivery_events(chat_id, kind, delivery_key, status, created_at, sent_at) "
                "VALUES (?, ?, ?, 'sent', ?, ?) "
                "ON CONFLICT(chat_id, kind, delivery_key) DO UPDATE SET "
                "status='sent', sent_at=excluded.sent_at",
                (chat_id, kind, key, now, now),
            )

    def release_delivery(self, chat_id: int, kind: str, key: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM delivery_events WHERE chat_id = ? AND kind = ? "
                "AND delivery_key = ? AND status = 'pending'",
                (chat_id, kind, key),
            )

    def sent_count_since(self, chat_id: int, kind: str, utc_start: datetime) -> int:
        with self.connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM delivery_events WHERE chat_id = ? AND kind = ? "
                "AND status = 'sent' AND sent_at >= ?",
                (chat_id, kind, utc_start.astimezone(timezone.utc).isoformat()),
            ).fetchone()[0]

    def latest_delivery(self, chat_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT kind, created_at, sent_at FROM delivery_events "
                "WHERE chat_id = ? AND status = 'sent' ORDER BY sent_at DESC LIMIT 1",
                (chat_id,),
            ).fetchone()
            return dict(row) if row else None

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
            conn.execute("DELETE FROM news_history WHERE first_seen < ?", (cutoff,))
            conn.execute("DELETE FROM delivery_events WHERE created_at < ?", (cutoff,))

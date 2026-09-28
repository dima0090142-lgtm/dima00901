import time
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    first_name  TEXT,
    username    TEXT,
    created_at  INTEGER NOT NULL,
    blocked     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS applications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    name        TEXT NOT NULL,
    phone       TEXT NOT NULL,
    idea        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'new',   -- new | contacted | scheduled | rejected
    created_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS appointments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id  INTEGER,
    user_id         INTEGER NOT NULL,
    name            TEXT NOT NULL,
    phone           TEXT NOT NULL,
    starts_at       INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'scheduled',  -- scheduled | came | no_show | cancelled
    reminded        INTEGER NOT NULL DEFAULT 0,
    asked           INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS faq (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    question  TEXT NOT NULL,
    answer    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS promos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS portfolio (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "about": (
        "Тату-Культ — место, где искусство останется с Вами навсегда.\n\n"
        "Мастер — Дарья. Каждый эскиз создаётся индивидуально: "
        "обсудим идею на консультации и подберём стиль, размер и место."
    ),
    "address": "Владивосток, Светланская 23, стр. 2, 3 этаж",
    "contacts": "Запись и вопросы — через это приложение или в личные сообщения Telegram.",
}

DEFAULT_FAQ = [
    (
        "Сколько стоит татуировка?",
        "Стоимость рассчитывается индивидуально и зависит от размера, сложности и места. "
        "Точную цену назовём на консультации.",
    ),
    (
        "Больно ли делать тату?",
        "Ощущения зависят от зоны и индивидуального порога чувствительности. "
        "Большинство клиентов переносят процесс комфортно.",
    ),
    (
        "Как подготовиться к сеансу?",
        "Хорошо выспитесь и поешьте, не употребляйте алкоголь за сутки до сеанса. "
        "Возьмите удобную одежду, открывающую нужную зону.",
    ),
    (
        "Как ухаживать за татуировкой?",
        "Подробную памятку по уходу мастер выдаст после сеанса. "
        "Первые недели — без бани, бассейна и загара.",
    ),
]


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        for key, value in DEFAULT_SETTINGS.items():
            await self.conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value)
            )
        # FAQ заполняется примерами один раз — если админ их удалит, они не вернутся
        if await self.get_setting("faq_seeded") is None:
            for q, a in DEFAULT_FAQ:
                await self.conn.execute("INSERT INTO faq (question, answer) VALUES (?, ?)", (q, a))
            await self.conn.execute("INSERT INTO settings (key, value) VALUES ('faq_seeded', '1')")
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    async def fetchall(self, sql: str, *args: Any) -> list[dict]:
        async with self.conn.execute(sql, args) as cur:
            return [dict(row) for row in await cur.fetchall()]

    async def fetchone(self, sql: str, *args: Any) -> dict | None:
        async with self.conn.execute(sql, args) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def execute(self, sql: str, *args: Any) -> int:
        cur = await self.conn.execute(sql, args)
        await self.conn.commit()
        return cur.lastrowid

    # --- пользователи ---

    async def upsert_user(self, user_id: int, first_name: str | None, username: str | None) -> None:
        await self.execute(
            """INSERT INTO users (id, first_name, username, created_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET first_name = excluded.first_name,
                                             username = excluded.username,
                                             blocked = 0""",
            user_id, first_name, username, int(time.time()),
        )

    async def active_user_ids(self) -> list[int]:
        rows = await self.fetchall("SELECT id FROM users WHERE blocked = 0")
        return [r["id"] for r in rows]

    async def mark_blocked(self, user_id: int) -> None:
        await self.execute("UPDATE users SET blocked = 1 WHERE id = ?", user_id)

    # --- заявки ---

    async def add_application(self, user_id: int, name: str, phone: str, idea: str) -> int:
        return await self.execute(
            "INSERT INTO applications (user_id, name, phone, idea, created_at) VALUES (?, ?, ?, ?, ?)",
            user_id, name, phone, idea, int(time.time()),
        )

    async def recent_applications_count(self, user_id: int, seconds: int) -> int:
        row = await self.fetchone(
            "SELECT COUNT(*) AS n FROM applications WHERE user_id = ? AND created_at > ?",
            user_id, int(time.time()) - seconds,
        )
        return row["n"]

    async def get_application(self, app_id: int) -> dict | None:
        return await self.fetchone("SELECT * FROM applications WHERE id = ?", app_id)

    async def set_application_status(self, app_id: int, status: str) -> None:
        await self.execute("UPDATE applications SET status = ? WHERE id = ?", status, app_id)

    async def open_applications(self, limit: int = 10) -> list[dict]:
        return await self.fetchall(
            "SELECT * FROM applications WHERE status IN ('new', 'contacted') ORDER BY id DESC LIMIT ?",
            limit,
        )

    # --- сеансы ---

    async def add_appointment(self, application: dict, starts_at: int, reminded: bool) -> int:
        return await self.execute(
            """INSERT INTO appointments (application_id, user_id, name, phone, starts_at, reminded)
               VALUES (?, ?, ?, ?, ?, ?)""",
            application["id"], application["user_id"], application["name"],
            application["phone"], starts_at, int(reminded),
        )

    async def get_appointment(self, appt_id: int) -> dict | None:
        return await self.fetchone("SELECT * FROM appointments WHERE id = ?", appt_id)

    async def set_appointment_status(self, appt_id: int, status: str) -> None:
        await self.execute("UPDATE appointments SET status = ? WHERE id = ?", status, appt_id)

    async def upcoming_appointments(self) -> list[dict]:
        return await self.fetchall(
            "SELECT * FROM appointments WHERE status = 'scheduled' ORDER BY starts_at LIMIT 20"
        )

    async def appointments_to_remind(self, now: int) -> list[dict]:
        return await self.fetchall(
            """SELECT * FROM appointments
               WHERE status = 'scheduled' AND reminded = 0 AND starts_at - 86400 <= ? AND starts_at > ?""",
            now, now,
        )

    async def appointments_to_ask(self, now: int, delay: int) -> list[dict]:
        return await self.fetchall(
            "SELECT * FROM appointments WHERE status = 'scheduled' AND asked = 0 AND starts_at + ? <= ?",
            delay, now,
        )

    async def mark_reminded(self, appt_id: int) -> None:
        await self.execute("UPDATE appointments SET reminded = 1 WHERE id = ?", appt_id)

    async def mark_asked(self, appt_id: int) -> None:
        await self.execute("UPDATE appointments SET asked = 1 WHERE id = ?", appt_id)

    # --- контент ---

    async def get_setting(self, key: str) -> str | None:
        row = await self.fetchone("SELECT value FROM settings WHERE key = ?", key)
        return row["value"] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        await self.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            key, value,
        )

    async def list_faq(self) -> list[dict]:
        return await self.fetchall("SELECT * FROM faq ORDER BY id")

    async def add_faq(self, question: str, answer: str) -> None:
        await self.execute("INSERT INTO faq (question, answer) VALUES (?, ?)", question, answer)

    async def delete_faq(self, faq_id: int) -> None:
        await self.execute("DELETE FROM faq WHERE id = ?", faq_id)

    async def list_promos(self) -> list[dict]:
        return await self.fetchall("SELECT * FROM promos ORDER BY id DESC")

    async def add_promo(self, text: str) -> None:
        await self.execute("INSERT INTO promos (text, created_at) VALUES (?, ?)", text, int(time.time()))

    async def delete_promo(self, promo_id: int) -> None:
        await self.execute("DELETE FROM promos WHERE id = ?", promo_id)

    async def list_portfolio(self) -> list[dict]:
        return await self.fetchall("SELECT * FROM portfolio ORDER BY id DESC")

    async def add_portfolio(self, filename: str) -> None:
        await self.execute(
            "INSERT INTO portfolio (filename, created_at) VALUES (?, ?)", filename, int(time.time())
        )

    async def delete_portfolio(self, photo_id: int) -> None:
        await self.execute("DELETE FROM portfolio WHERE id = ?", photo_id)

import re
import secrets
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
CREATE TABLE IF NOT EXISTS clients (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    phone        TEXT NOT NULL UNIQUE,   -- только цифры, 7XXXXXXXXXX
    name         TEXT NOT NULL,
    user_id      INTEGER,                -- Telegram ID, когда клиент подключился к боту
    note         TEXT NOT NULL DEFAULT '',
    invite_code  TEXT NOT NULL UNIQUE,
    created_at   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    amount      INTEGER NOT NULL,             -- рубли
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending | claimed | paid | cancelled
    created_at  INTEGER NOT NULL,
    claimed_at  INTEGER,
    paid_at     INTEGER
);
CREATE TABLE IF NOT EXISTS posts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    src_chat      INTEGER,                 -- откуда копировать пост (чат админа с ботом)
    src_message   INTEGER,
    kind          TEXT NOT NULL DEFAULT 'text',  -- text | photo | media
    text          TEXT NOT NULL DEFAULT '',
    photo_file_id TEXT,
    ai_brief      TEXT,
    with_button   INTEGER NOT NULL DEFAULT 1,
    status        TEXT NOT NULL DEFAULT 'draft',  -- draft | scheduled | published | cancelled
    source        TEXT NOT NULL DEFAULT 'bot',    -- bot | channel (опубликован в канале вручную)
    publish_at    INTEGER,
    published_at  INTEGER,
    created_at    INTEGER NOT NULL
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
    "aftercare": (
        "Спасибо, что выбрали Тату-Культ! 🖤\n\n"
        "<b>Уход за татуировкой</b>\n"
        "• Плёнку снимите через время, которое назвал мастер.\n"
        "• Промывайте тату тёплой водой с мылом 2–3 раза в день.\n"
        "• Наносите тонкий слой заживляющего крема.\n"
        "• 2–3 недели: без бани, сауны, бассейна и загара.\n"
        "• Не сдирайте корочки и не расчёсывайте.\n\n"
        "Если что-то беспокоит — просто напишите нам."
    ),
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


# Клиент «подключён», если нажал «Старт» в боте и не заблокировал его — только тогда бот может ему писать
CLIENT_SELECT = """SELECT c.*, (u.id IS NOT NULL AND u.blocked = 0) AS started
                   FROM clients c LEFT JOIN users u ON u.id = c.user_id"""

# Кому писать о сеансе: тому, кто оставил заявку, или клиенту, который подключился к боту позже
CHAT_ID = "COALESCE(NULLIF(a.user_id, 0), c.user_id)"


def normalize_phone(raw: str) -> str:
    """Приводит номер к виду 7XXXXXXXXXX, чтобы +7 999…, 8 999… и 999… считались одним номером."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    return digits


def format_phone(phone: str) -> str:
    if len(phone) == 11 and phone.startswith("7"):
        return f"+7 {phone[1:4]} {phone[4:7]}-{phone[7:9]}-{phone[9:]}"
    return "+" + phone if phone else ""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self._add_column("applications", "client_id", "INTEGER")
        await self._add_column("appointments", "client_id", "INTEGER")
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

    async def _add_column(self, table: str, column: str, decl: str) -> None:
        """Добавляет колонку в уже существующую базу (обновление без потери данных)."""
        async with self.conn.execute(f"PRAGMA table_info({table})") as cur:
            columns = {row[1] for row in await cur.fetchall()}
        if column not in columns:
            await self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

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

    async def add_application(
        self, user_id: int, name: str, phone: str, idea: str, client_id: int | None = None, status: str = "new"
    ) -> int:
        return await self.execute(
            """INSERT INTO applications (user_id, name, phone, idea, created_at, client_id, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            user_id, name, phone, idea, int(time.time()), client_id, status,
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
            """INSERT INTO appointments (application_id, user_id, name, phone, starts_at, reminded, client_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            application["id"], application["user_id"], application["name"],
            application["phone"], starts_at, int(reminded), application.get("client_id"),
        )

    async def get_appointment(self, appt_id: int) -> dict | None:
        return await self.fetchone("SELECT * FROM appointments WHERE id = ?", appt_id)

    async def set_appointment_status(self, appt_id: int, status: str) -> None:
        await self.execute("UPDATE appointments SET status = ? WHERE id = ?", status, appt_id)

    async def upcoming_appointments(self) -> list[dict]:
        return await self.fetchall(
            "SELECT * FROM appointments WHERE status = 'scheduled' ORDER BY starts_at LIMIT 20"
        )

    async def appointments_between(self, start: int, end: int) -> list[dict]:
        return await self.fetchall(
            """SELECT * FROM appointments
               WHERE status IN ('scheduled', 'came') AND starts_at >= ? AND starts_at < ? ORDER BY starts_at""",
            start, end,
        )

    async def appointments_to_remind(self, now: int) -> list[dict]:
        return await self.fetchall(
            f"""SELECT a.*, {CHAT_ID} AS chat_id FROM appointments a LEFT JOIN clients c ON c.id = a.client_id
               WHERE a.status = 'scheduled' AND a.reminded = 0 AND a.starts_at - 86400 <= ? AND a.starts_at > ?""",
            now, now,
        )

    async def appointment_chat_id(self, appt_id: int) -> int | None:
        row = await self.fetchone(
            f"""SELECT {CHAT_ID} AS chat_id FROM appointments a LEFT JOIN clients c ON c.id = a.client_id
               WHERE a.id = ?""",
            appt_id,
        )
        return row["chat_id"] if row else None

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

    # --- клиенты ---

    async def upsert_client(self, phone: str, name: str, user_id: int | None = None) -> dict | None:
        """Находит клиента по номеру или создаёт нового. Telegram ID привязывается, если его ещё нет."""
        norm = normalize_phone(phone)
        if len(norm) < 10:
            return None
        client = await self.fetchone("SELECT * FROM clients WHERE phone = ?", norm)
        if client is None:
            await self.execute(
                "INSERT INTO clients (phone, name, user_id, invite_code, created_at) VALUES (?, ?, ?, ?, ?)",
                norm, name.strip()[:100] or "Без имени", user_id, secrets.token_hex(5), int(time.time()),
            )
        elif user_id and not client["user_id"]:
            await self.execute("UPDATE clients SET user_id = ? WHERE id = ?", user_id, client["id"])
        return await self.fetchone("SELECT * FROM clients WHERE phone = ?", norm)

    async def get_client(self, client_id: int) -> dict | None:
        return await self.fetchone(f"{CLIENT_SELECT} WHERE c.id = ?", client_id)

    async def client_by_invite(self, code: str) -> dict | None:
        return await self.fetchone("SELECT * FROM clients WHERE invite_code = ?", code)

    async def client_by_user(self, user_id: int) -> dict | None:
        return await self.fetchone("SELECT * FROM clients WHERE user_id = ?", user_id)

    async def link_client(self, client_id: int, user_id: int) -> None:
        await self.execute("UPDATE clients SET user_id = ? WHERE id = ?", user_id, client_id)

    async def find_clients(self, query: str, limit: int = 10) -> list[dict]:
        digits = re.sub(r"\D", "", query)
        if len(digits) >= 3:
            if len(digits) >= 10:
                digits = normalize_phone(digits)
            return await self.fetchall(
                f"{CLIENT_SELECT} WHERE c.phone LIKE ? ORDER BY c.id DESC LIMIT ?", f"%{digits}%", limit
            )
        return await self.fetchall(
            f"{CLIENT_SELECT} WHERE c.name LIKE ? ORDER BY c.id DESC LIMIT ?", f"%{query.strip()}%", limit
        )

    async def list_clients(self, limit: int, offset: int) -> list[dict]:
        return await self.fetchall(f"{CLIENT_SELECT} ORDER BY c.id DESC LIMIT ? OFFSET ?", limit, offset)

    async def count_clients(self) -> tuple[int, int]:
        row = await self.fetchone(f"SELECT COUNT(*) AS total, COALESCE(SUM(started), 0) AS linked FROM ({CLIENT_SELECT})")
        return row["total"], row["linked"]

    async def set_client_note(self, client_id: int, note: str) -> None:
        await self.execute("UPDATE clients SET note = ? WHERE id = ?", note, client_id)

    async def set_client_name(self, client_id: int, name: str) -> None:
        await self.execute("UPDATE clients SET name = ? WHERE id = ?", name, client_id)

    async def delete_client(self, client_id: int) -> None:
        await self.execute("UPDATE applications SET client_id = NULL WHERE client_id = ?", client_id)
        await self.execute("UPDATE appointments SET client_id = NULL WHERE client_id = ?", client_id)
        await self.execute("DELETE FROM clients WHERE id = ?", client_id)

    async def client_appointments(self, client_id: int) -> list[dict]:
        return await self.fetchall(
            "SELECT * FROM appointments WHERE client_id = ? ORDER BY starts_at DESC LIMIT 10", client_id
        )

    async def client_applications_count(self, client_id: int) -> int:
        row = await self.fetchone("SELECT COUNT(*) AS n FROM applications WHERE client_id = ?", client_id)
        return row["n"]

    # --- предоплаты ---

    async def add_payment(self, client_id: int, user_id: int, amount: int) -> int:
        return await self.execute(
            "INSERT INTO payments (client_id, user_id, amount, created_at) VALUES (?, ?, ?, ?)",
            client_id, user_id, amount, int(time.time()),
        )

    async def get_payment(self, payment_id: int) -> dict | None:
        return await self.fetchone(
            """SELECT p.*, c.name, c.phone FROM payments p JOIN clients c ON c.id = p.client_id
               WHERE p.id = ?""",
            payment_id,
        )

    async def set_payment_status(self, payment_id: int, status: str) -> None:
        column = {"claimed": "claimed_at", "paid": "paid_at"}.get(status)
        if column:
            await self.execute(
                f"UPDATE payments SET status = ?, {column} = ? WHERE id = ?", status, int(time.time()), payment_id
            )
        else:
            await self.execute("UPDATE payments SET status = ? WHERE id = ?", status, payment_id)

    async def client_payments(self, client_id: int) -> list[dict]:
        return await self.fetchall(
            "SELECT * FROM payments WHERE client_id = ? AND status != 'cancelled' ORDER BY id DESC LIMIT 5",
            client_id,
        )

    async def last_claimed_payment(self, user_id: int, within: int) -> dict | None:
        return await self.fetchone(
            """SELECT p.*, c.name, c.phone FROM payments p JOIN clients c ON c.id = p.client_id
               WHERE p.user_id = ? AND p.status = 'claimed' AND p.claimed_at > ? ORDER BY p.id DESC""",
            user_id, int(time.time()) - within,
        )

    # --- посты в канал ---

    async def add_post(self, src_chat: int | None, src_message: int | None, kind: str = "text", text: str = "",
                       photo_file_id: str | None = None, ai_brief: str | None = None,
                       status: str = "draft", source: str = "bot") -> int:
        now = int(time.time())
        return await self.execute(
            """INSERT INTO posts (src_chat, src_message, kind, text, photo_file_id, ai_brief, status, source,
                                  published_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            src_chat, src_message, kind, text, photo_file_id, ai_brief, status, source,
            now if status == "published" else None, now,
        )

    async def get_post(self, post_id: int) -> dict | None:
        return await self.fetchone("SELECT * FROM posts WHERE id = ?", post_id)

    async def update_post(self, post_id: int, **fields) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        await self.execute(f"UPDATE posts SET {cols} WHERE id = ?", *fields.values(), post_id)

    async def due_posts(self, now: int) -> list[dict]:
        return await self.fetchall(
            "SELECT * FROM posts WHERE status = 'scheduled' AND publish_at <= ? ORDER BY publish_at", now
        )

    async def scheduled_posts(self) -> list[dict]:
        return await self.fetchall("SELECT * FROM posts WHERE status = 'scheduled' ORDER BY publish_at LIMIT 20")

    async def last_published_at(self) -> int | None:
        row = await self.fetchone("SELECT MAX(published_at) AS t FROM posts WHERE status = 'published'")
        return row["t"]

    async def recent_post_texts(self, limit: int = 5) -> list[str]:
        rows = await self.fetchall(
            """SELECT text FROM posts WHERE status = 'published' AND text != ''
               ORDER BY published_at DESC LIMIT ?""",
            limit,
        )
        return [r["text"] for r in rows]

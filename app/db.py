import re
import secrets
import time
from typing import Any

import asyncpg

# Общая база студии: её используют бот, мини-приложение и админка сайта.
# Время хранится в секундах Unix (BIGINT), как и раньше в SQLite.
SCHEMA = """
CREATE TABLE IF NOT EXISTS masters (
    id           SERIAL PRIMARY KEY,
    slug         TEXT NOT NULL UNIQUE,              -- логин в админке сайта: daria, alexa, dima
    name         TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'master',    -- master | admin
    telegram_id  BIGINT UNIQUE,
    active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id          BIGINT PRIMARY KEY,                 -- Telegram ID
    first_name  TEXT,
    username    TEXT,
    created_at  BIGINT NOT NULL,
    blocked     BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE TABLE IF NOT EXISTS clients (
    id           SERIAL PRIMARY KEY,
    phone        TEXT NOT NULL UNIQUE,              -- только цифры, 7XXXXXXXXXX
    name         TEXT NOT NULL,
    user_id      BIGINT,                            -- Telegram ID, когда клиент подключился к боту
    note         TEXT NOT NULL DEFAULT '',
    invite_code  TEXT NOT NULL UNIQUE,
    created_at   BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS applications (
    id          SERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL,                    -- 0, если заявка не из Telegram
    client_id   INTEGER REFERENCES clients(id) ON DELETE SET NULL,
    master_id   INTEGER REFERENCES masters(id) ON DELETE SET NULL,
    name        TEXT NOT NULL,
    phone       TEXT NOT NULL,
    idea        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'new',        -- new | contacted | scheduled | rejected | admin
    source      TEXT NOT NULL DEFAULT 'miniapp',    -- miniapp | bot | site
    created_at  BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS appointments (
    id              SERIAL PRIMARY KEY,
    application_id  INTEGER REFERENCES applications(id) ON DELETE SET NULL,
    client_id       INTEGER REFERENCES clients(id) ON DELETE SET NULL,
    master_id       INTEGER REFERENCES masters(id) ON DELETE SET NULL,
    user_id         BIGINT NOT NULL DEFAULT 0,
    name            TEXT NOT NULL,
    phone           TEXT NOT NULL,
    starts_at       BIGINT NOT NULL,
    ends_at         BIGINT,
    notes           TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'scheduled',  -- scheduled | came | no_show | cancelled
    source          TEXT NOT NULL DEFAULT 'bot',        -- bot | site
    reminded        BOOLEAN NOT NULL DEFAULT FALSE,
    asked           BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM now())::BIGINT
);
CREATE INDEX IF NOT EXISTS appointments_starts_idx ON appointments (starts_at);
CREATE INDEX IF NOT EXISTS appointments_client_idx ON appointments (client_id);
CREATE INDEX IF NOT EXISTS applications_user_idx ON applications (user_id, created_at);
CREATE TABLE IF NOT EXISTS supplies (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,
    quantity    NUMERIC NOT NULL DEFAULT 0,
    unit        TEXT NOT NULL,
    created_at  BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS faq (
    id        SERIAL PRIMARY KEY,
    question  TEXT NOT NULL,
    answer    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS promos (
    id          SERIAL PRIMARY KEY,
    text        TEXT NOT NULL,
    created_at  BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS portfolio (
    id          SERIAL PRIMARY KEY,
    filename    TEXT NOT NULL,
    created_at  BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""

# Мастера студии (как на сайте). Первый — мастер по умолчанию для заявок из бота.
DEFAULT_MASTERS = [
    ("daria", "Дарья", "master"),
    ("alexa", "Александра", "master"),
    ("dima", "Дмитрий", "master"),
    ("admin", "Администратор", "admin"),
]

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
CLIENT_SELECT = """SELECT c.*, (u.id IS NOT NULL AND NOT u.blocked) AS started
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


def _now() -> int:
    return int(time.time())


class Database:
    def __init__(self, dsn: str, default_master: str = "daria"):
        self.dsn = dsn
        self.default_master = default_master
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)
        async with self.pool.acquire() as conn, conn.transaction():
            # Блокировка, чтобы два процесса (бот и API сайта) не создавали схему одновременно
            await conn.execute("SELECT pg_advisory_xact_lock(4242001)")
            await conn.execute(SCHEMA)
            for slug, name, role in DEFAULT_MASTERS:
                await conn.execute(
                    "INSERT INTO masters (slug, name, role, created_at) VALUES ($1, $2, $3, $4) "
                    "ON CONFLICT (slug) DO NOTHING",
                    slug, name, role, _now(),
                )
            for key, value in DEFAULT_SETTINGS.items():
                await conn.execute(
                    "INSERT INTO settings (key, value) VALUES ($1, $2) ON CONFLICT (key) DO NOTHING", key, value
                )
            # FAQ заполняется примерами один раз — если админ их удалит, они не вернутся
            seeded = await conn.fetchval("SELECT value FROM settings WHERE key = 'faq_seeded'")
            if seeded is None:
                await conn.executemany(
                    "INSERT INTO faq (question, answer) VALUES ($1, $2)", DEFAULT_FAQ
                )
                await conn.execute("INSERT INTO settings (key, value) VALUES ('faq_seeded', '1')")

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    async def fetchall(self, sql: str, *args: Any) -> list[dict]:
        return [dict(r) for r in await self.pool.fetch(sql, *args)]

    async def fetchone(self, sql: str, *args: Any) -> dict | None:
        row = await self.pool.fetchrow(sql, *args)
        return dict(row) if row else None

    async def fetchval(self, sql: str, *args: Any) -> Any:
        return await self.pool.fetchval(sql, *args)

    async def execute(self, sql: str, *args: Any) -> None:
        await self.pool.execute(sql, *args)

    # --- мастера ---

    async def default_master_id(self) -> int | None:
        return await self.fetchval("SELECT id FROM masters WHERE slug = $1", self.default_master)

    async def list_masters(self) -> list[dict]:
        return await self.fetchall("SELECT * FROM masters WHERE active ORDER BY id")

    # --- пользователи ---

    async def upsert_user(self, user_id: int, first_name: str | None, username: str | None) -> None:
        await self.execute(
            """INSERT INTO users (id, first_name, username, created_at) VALUES ($1, $2, $3, $4)
               ON CONFLICT (id) DO UPDATE SET first_name = EXCLUDED.first_name,
                                              username = EXCLUDED.username,
                                              blocked = FALSE""",
            user_id, first_name, username, _now(),
        )

    async def active_user_ids(self) -> list[int]:
        rows = await self.fetchall("SELECT id FROM users WHERE NOT blocked")
        return [r["id"] for r in rows]

    async def mark_blocked(self, user_id: int) -> None:
        await self.execute("UPDATE users SET blocked = TRUE WHERE id = $1", user_id)

    # --- заявки ---

    async def add_application(
        self, user_id: int, name: str, phone: str, idea: str, client_id: int | None = None,
        status: str = "new", master_id: int | None = None, source: str = "miniapp",
    ) -> int:
        if master_id is None:
            master_id = await self.default_master_id()
        return await self.fetchval(
            """INSERT INTO applications (user_id, name, phone, idea, created_at, client_id, status, master_id, source)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING id""",
            user_id, name, phone, idea, _now(), client_id, status, master_id, source,
        )

    async def recent_applications_count(self, user_id: int, seconds: int) -> int:
        return await self.fetchval(
            "SELECT COUNT(*) FROM applications WHERE user_id = $1 AND created_at > $2",
            user_id, _now() - seconds,
        )

    async def get_application(self, app_id: int) -> dict | None:
        return await self.fetchone("SELECT * FROM applications WHERE id = $1", app_id)

    async def set_application_status(self, app_id: int, status: str) -> None:
        await self.execute("UPDATE applications SET status = $1 WHERE id = $2", status, app_id)

    async def open_applications(self, limit: int = 10) -> list[dict]:
        return await self.fetchall(
            "SELECT * FROM applications WHERE status IN ('new', 'contacted') ORDER BY id DESC LIMIT $1",
            limit,
        )

    # --- сеансы ---

    async def add_appointment(self, application: dict, starts_at: int, reminded: bool) -> int:
        master_id = application.get("master_id") or await self.default_master_id()
        return await self.fetchval(
            """INSERT INTO appointments (application_id, user_id, name, phone, starts_at, reminded, client_id, master_id)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id""",
            application["id"], application["user_id"], application["name"],
            application["phone"], starts_at, reminded, application.get("client_id"), master_id,
        )

    async def get_appointment(self, appt_id: int) -> dict | None:
        return await self.fetchone("SELECT * FROM appointments WHERE id = $1", appt_id)

    async def set_appointment_status(self, appt_id: int, status: str) -> None:
        await self.execute("UPDATE appointments SET status = $1 WHERE id = $2", status, appt_id)

    async def upcoming_appointments(self) -> list[dict]:
        return await self.fetchall(
            "SELECT * FROM appointments WHERE status = 'scheduled' ORDER BY starts_at LIMIT 20"
        )

    async def appointments_between(self, start: int, end: int) -> list[dict]:
        return await self.fetchall(
            """SELECT * FROM appointments
               WHERE status IN ('scheduled', 'came') AND starts_at >= $1 AND starts_at < $2 ORDER BY starts_at""",
            start, end,
        )

    async def appointments_to_remind(self, now: int) -> list[dict]:
        return await self.fetchall(
            f"""SELECT a.*, {CHAT_ID} AS chat_id FROM appointments a LEFT JOIN clients c ON c.id = a.client_id
               WHERE a.status = 'scheduled' AND NOT a.reminded AND a.starts_at - 86400 <= $1 AND a.starts_at > $1""",
            now,
        )

    async def appointment_chat_id(self, appt_id: int) -> int | None:
        return await self.fetchval(
            f"""SELECT {CHAT_ID} FROM appointments a LEFT JOIN clients c ON c.id = a.client_id
               WHERE a.id = $1""",
            appt_id,
        )

    async def appointments_to_ask(self, now: int, delay: int) -> list[dict]:
        return await self.fetchall(
            "SELECT * FROM appointments WHERE status = 'scheduled' AND NOT asked AND starts_at + $1 <= $2",
            delay, now,
        )

    async def mark_reminded(self, appt_id: int) -> None:
        await self.execute("UPDATE appointments SET reminded = TRUE WHERE id = $1", appt_id)

    async def mark_asked(self, appt_id: int) -> None:
        await self.execute("UPDATE appointments SET asked = TRUE WHERE id = $1", appt_id)

    # --- контент ---

    async def get_setting(self, key: str) -> str | None:
        return await self.fetchval("SELECT value FROM settings WHERE key = $1", key)

    async def set_setting(self, key: str, value: str) -> None:
        await self.execute(
            "INSERT INTO settings (key, value) VALUES ($1, $2) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            key, value,
        )

    async def delete_setting(self, key: str) -> None:
        await self.execute("DELETE FROM settings WHERE key = $1", key)

    async def list_faq(self) -> list[dict]:
        return await self.fetchall("SELECT * FROM faq ORDER BY id")

    async def add_faq(self, question: str, answer: str) -> None:
        await self.execute("INSERT INTO faq (question, answer) VALUES ($1, $2)", question, answer)

    async def delete_faq(self, faq_id: int) -> None:
        await self.execute("DELETE FROM faq WHERE id = $1", faq_id)

    async def list_promos(self) -> list[dict]:
        return await self.fetchall("SELECT * FROM promos ORDER BY id DESC")

    async def add_promo(self, text: str) -> None:
        await self.execute("INSERT INTO promos (text, created_at) VALUES ($1, $2)", text, _now())

    async def delete_promo(self, promo_id: int) -> None:
        await self.execute("DELETE FROM promos WHERE id = $1", promo_id)

    async def list_portfolio(self) -> list[dict]:
        return await self.fetchall("SELECT * FROM portfolio ORDER BY id DESC")

    async def get_portfolio(self, photo_id: int) -> dict | None:
        return await self.fetchone("SELECT * FROM portfolio WHERE id = $1", photo_id)

    async def add_portfolio(self, filename: str) -> None:
        await self.execute("INSERT INTO portfolio (filename, created_at) VALUES ($1, $2)", filename, _now())

    async def delete_portfolio(self, photo_id: int) -> None:
        await self.execute("DELETE FROM portfolio WHERE id = $1", photo_id)

    # --- клиенты ---

    async def upsert_client(self, phone: str, name: str, user_id: int | None = None) -> dict | None:
        """Находит клиента по номеру или создаёт нового. Telegram ID привязывается, если его ещё нет."""
        norm = normalize_phone(phone)
        if len(norm) < 10:
            return None
        return await self.fetchone(
            """INSERT INTO clients (phone, name, user_id, invite_code, created_at) VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (phone) DO UPDATE SET user_id = COALESCE(clients.user_id, EXCLUDED.user_id)
               RETURNING *""",
            norm, name.strip()[:100] or "Без имени", user_id or None, secrets.token_hex(5), _now(),
        )

    async def get_client(self, client_id: int) -> dict | None:
        return await self.fetchone(f"{CLIENT_SELECT} WHERE c.id = $1", client_id)

    async def client_by_invite(self, code: str) -> dict | None:
        return await self.fetchone("SELECT * FROM clients WHERE invite_code = $1", code)

    async def client_by_user(self, user_id: int) -> dict | None:
        return await self.fetchone("SELECT * FROM clients WHERE user_id = $1 LIMIT 1", user_id)

    async def link_client(self, client_id: int, user_id: int) -> None:
        await self.execute("UPDATE clients SET user_id = $1 WHERE id = $2", user_id, client_id)

    async def find_clients(self, query: str, limit: int = 10) -> list[dict]:
        digits = re.sub(r"\D", "", query)
        if len(digits) >= 3:
            if len(digits) >= 10:
                digits = normalize_phone(digits)
            return await self.fetchall(
                f"{CLIENT_SELECT} WHERE c.phone LIKE $1 ORDER BY c.id DESC LIMIT $2", f"%{digits}%", limit
            )
        return await self.fetchall(
            f"{CLIENT_SELECT} WHERE c.name ILIKE $1 ORDER BY c.id DESC LIMIT $2", f"%{query.strip()}%", limit
        )

    async def list_clients(self, limit: int, offset: int) -> list[dict]:
        return await self.fetchall(f"{CLIENT_SELECT} ORDER BY c.id DESC LIMIT $1 OFFSET $2", limit, offset)

    async def count_clients(self) -> tuple[int, int]:
        row = await self.fetchone(
            f"SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE started) AS linked FROM ({CLIENT_SELECT}) t"
        )
        return row["total"], row["linked"]

    async def set_client_note(self, client_id: int, note: str) -> None:
        await self.execute("UPDATE clients SET note = $1 WHERE id = $2", note, client_id)

    async def set_client_name(self, client_id: int, name: str) -> None:
        await self.execute("UPDATE clients SET name = $1 WHERE id = $2", name, client_id)

    async def delete_client(self, client_id: int) -> None:
        # Заявки и сеансы остаются, ссылка на клиента обнуляется (ON DELETE SET NULL)
        await self.execute("DELETE FROM clients WHERE id = $1", client_id)

    async def client_appointments(self, client_id: int) -> list[dict]:
        return await self.fetchall(
            "SELECT * FROM appointments WHERE client_id = $1 ORDER BY starts_at DESC LIMIT 10", client_id
        )

    async def client_applications_count(self, client_id: int) -> int:
        return await self.fetchval("SELECT COUNT(*) FROM applications WHERE client_id = $1", client_id)

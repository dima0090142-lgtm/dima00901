"""Перенос данных бота из старой базы SQLite (bot.db) в PostgreSQL.

Запуск (один раз, на пустую базу):
    DATABASE_URL=postgresql://... python scripts/import_sqlite.py /data/bot.db

ID записей сохраняются, поэтому ссылки-приглашения и кнопки в старых сообщениях продолжат работать.
"""
import asyncio
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from app.db import Database  # noqa: E402

TABLES = ["users", "clients", "applications", "appointments", "faq", "promos", "portfolio"]
BOOL_COLUMNS = {"blocked", "reminded", "asked"}


def read_sqlite(path: Path) -> dict[str, list[dict]]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    data = {}
    for table in TABLES + ["settings"]:
        try:
            data[table] = [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
        except sqlite3.OperationalError:
            data[table] = []
    conn.close()
    return data


async def main(sqlite_path: Path) -> None:
    load_dotenv()
    dsn = os.environ["DATABASE_URL"]
    data = read_sqlite(sqlite_path)

    db = Database(dsn, os.getenv("DEFAULT_MASTER", "daria"))
    await db.connect()  # создаёт таблицы, если их ещё нет
    master_id = await db.default_master_id()

    async with db.pool.acquire() as conn, conn.transaction():
        for table in ("clients", "applications", "appointments"):
            if await conn.fetchval(f"SELECT COUNT(*) FROM {table}"):
                raise SystemExit(f"В PostgreSQL уже есть данные в «{table}» — перенос остановлен, чтобы ничего не задвоить.")

        client_ids = {c["id"] for c in data["clients"]}
        app_ids = {a["id"] for a in data["applications"]}
        for a in data["applications"]:
            a["master_id"] = master_id
            if a.get("client_id") not in client_ids:
                a["client_id"] = None
        for a in data["appointments"]:
            a["master_id"] = master_id
            if a.get("client_id") not in client_ids:
                a["client_id"] = None
            if a.get("application_id") not in app_ids:
                a["application_id"] = None

        # Примеры FAQ из новой базы заменяем вопросами, которые админ уже настроил
        await conn.execute("DELETE FROM faq")

        for table in TABLES:
            rows = data[table]
            if not rows:
                continue
            columns = list(rows[0].keys())
            placeholders = ", ".join(f"${i}" for i in range(1, len(columns) + 1))
            sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
            values = [
                [bool(r[c]) if c in BOOL_COLUMNS else r[c] for c in columns]
                for r in rows
            ]
            await conn.executemany(sql, values)
            if "id" in columns:
                await conn.execute(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), (SELECT MAX(id) FROM {table}))"
                )
            print(f"{table}: {len(rows)}")

        for s in data["settings"]:
            await conn.execute(
                "INSERT INTO settings (key, value) VALUES ($1, $2) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                s["key"], s["value"],
            )
        print(f"settings: {len(data['settings'])}")

    await db.close()
    print("Готово ✅")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Использование: python scripts/import_sqlite.py /путь/к/bot.db")
    asyncio.run(main(Path(sys.argv[1])))

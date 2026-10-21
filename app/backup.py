import asyncio
import json
import logging
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage

from aiogram import Bot

from .config import Config
from .db import Database, format_phone

log = logging.getLogger(__name__)

# Что попадает в копию: клиенты и всё, что связано с записями
TABLES = {
    "clients": "SELECT * FROM clients ORDER BY id",
    "applications": "SELECT * FROM applications ORDER BY id",
    "appointments": "SELECT * FROM appointments ORDER BY id",
    "payments": "SELECT * FROM payments ORDER BY id",
    "users": "SELECT * FROM users ORDER BY id",
}
TIME_FIELDS = {"created_at", "starts_at", "claimed_at", "paid_at", "came_at", "pd_consent_at"}


def _readable(row: dict, cfg: Config) -> dict:
    """Даты — в виде «2026-10-21 15:30», телефон — в виде «+7 999 123-45-67», чтобы файл было удобно читать."""
    for key in TIME_FIELDS & row.keys():
        if row[key]:
            row[key] = f"{datetime.fromtimestamp(row[key], cfg.tz):%Y-%m-%d %H:%M}"
    if row.get("phone") and row["phone"].isdigit():
        row["phone"] = format_phone(row["phone"])
    return row


async def build_backup(db: Database, cfg: Config) -> tuple[str, bytes]:
    """Возвращает имя файла и JSON с клиентами, заявками, сеансами и предоплатами."""
    now = datetime.now(cfg.tz)
    data = {"created": f"{now:%Y-%m-%d %H:%M}", "timezone": str(cfg.tz)}
    for name, sql in TABLES.items():
        data[name] = [_readable(row, cfg) for row in await db.fetchall(sql)]
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    return f"tattoo-backup-{now:%Y-%m-%d}.json", body


def email_enabled(cfg: Config) -> bool:
    return bool(cfg.smtp_user and cfg.smtp_password and cfg.backup_email)


def _send_email(cfg: Config, filename: str, body: bytes, summary: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = f"Тату-Культ: резервная копия {filename.removeprefix('tattoo-backup-').removesuffix('.json')}"
    msg["From"] = cfg.smtp_user
    msg["To"] = cfg.backup_email
    msg.set_content(f"Резервная копия базы клиентов и записей во вложении.\n\n{summary}")
    msg.add_attachment(body, maintype="application", subtype="json", filename=filename)
    context = ssl.create_default_context()
    if cfg.smtp_port == 465:
        with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=context, timeout=30) as smtp:
            smtp.login(cfg.smtp_user, cfg.smtp_password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as smtp:
            smtp.starttls(context=context)
            smtp.login(cfg.smtp_user, cfg.smtp_password)
            smtp.send_message(msg)


def summary_text(body: bytes) -> str:
    data = json.loads(body)
    return (
        f"Клиентов: {len(data['clients'])}, заявок: {len(data['applications'])}, "
        f"сеансов: {len(data['appointments'])}, предоплат: {len(data['payments'])}."
    )


async def email_backup(db: Database, cfg: Config) -> tuple[str, bytes]:
    """Собирает копию и отправляет её на почту. Ошибку отправки пробрасывает дальше."""
    filename, body = await build_backup(db, cfg)
    await asyncio.to_thread(_send_email, cfg, filename, body, summary_text(body))
    return filename, body


async def backup_tick(bot: Bot, db: Database, cfg: Config) -> None:
    """Раз в сутки (после BACKUP_HOUR по местному времени) отправляет копию на почту."""
    if not email_enabled(cfg):
        return
    now = datetime.now(cfg.tz)
    today = f"{now:%Y-%m-%d}"
    if now.hour < cfg.backup_hour or await db.get_setting("last_backup_date") == today:
        return
    # Отмечаем заранее: при ошибке не повторяем каждую минуту, следующая попытка — завтра
    await db.set_setting("last_backup_date", today)
    try:
        await email_backup(db, cfg)
        log.info("Резервная копия отправлена на %s", cfg.backup_email)
    except Exception as e:
        log.exception("Не удалось отправить резервную копию на почту")
        for admin_id in cfg.admin_ids:
            try:
                await bot.send_message(
                    admin_id,
                    f"⚠️ Не удалось отправить резервную копию на {cfg.backup_email}: {type(e).__name__}.\n"
                    "Проверьте SMTP_USER и SMTP_PASSWORD. Копию можно скачать вручную: "
                    "/admin → ⚙️ Настройки → 💾 Резервная копия.",
                )
            except Exception:
                pass

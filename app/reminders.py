import asyncio
import logging
import time
from datetime import datetime
from html import escape

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .ai import ContentAI
from .backup import backup_tick
from .common import fmt_dt, review_kb
from .config import Config
from .db import Database
from .handlers.channel import publish_due, reminder_tick

log = logging.getLogger(__name__)


async def reminder_loop(bot: Bot, db: Database, cfg: Config, ai: ContentAI) -> None:
    """Раз в минуту: напоминает о сеансе, спрашивает админа, пришёл ли клиент, просит отзыв, делает резервную копию."""
    while True:
        try:
            await tick(bot, db, cfg)
        except Exception:
            log.exception("Ошибка в цикле напоминаний")
        try:
            await publish_due(bot, db, cfg)
            await reminder_tick(bot, db, cfg, ai)
        except Exception:
            log.exception("Ошибка в задачах канала")
        try:
            await backup_tick(bot, db, cfg)
        except Exception:
            log.exception("Ошибка резервного копирования")
        await asyncio.sleep(60)


async def tick(bot: Bot, db: Database, cfg: Config) -> None:
    now = int(time.time())
    address = await db.get_setting("address") or ""

    for appt in await db.appointments_to_remind(now):
        await db.mark_reminded(appt["id"])
        if not appt["chat_id"]:
            continue
        try:
            await bot.send_message(
                appt["chat_id"],
                f"⏰ <b>Напоминание о сеансе</b>\n\n"
                f"Ждём вас {fmt_dt(appt['starts_at'], cfg.tz)}\n📍 {escape(address)}\n\n"
                "Хорошо выспитесь, поешьте и не употребляйте алкоголь. "
                "Если планы изменились — пожалуйста, предупредите нас.",
            )
        except Exception:
            log.warning("Не удалось отправить напоминание клиенту %s", appt["chat_id"])

    for appt in await db.appointments_to_ask(now, cfg.arrival_check_minutes * 60):
        await db.mark_asked(appt["id"])
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Пришёл", callback_data=f"appt:came:{appt['id']}"),
            InlineKeyboardButton(text="❌ Не пришёл", callback_data=f"appt:noshow:{appt['id']}"),
        ]])
        for admin_id in cfg.admin_ids:
            try:
                await bot.send_message(
                    admin_id,
                    f"❓ <b>Пришёл ли клиент?</b>\n\n"
                    f"👤 {escape(appt['name'])}, {escape(appt['phone'])}\n"
                    f"🗓 {fmt_dt(appt['starts_at'], cfg.tz)}",
                    reply_markup=markup,
                )
            except Exception:
                log.exception("Не удалось спросить админа %s", admin_id)

    # Просьба об отзыве — через несколько часов после сеанса и только днём, чтобы не будить клиента
    if 10 <= datetime.now(cfg.tz).hour < 21:
        await send_review_requests(bot, db, now, int(cfg.review_delay_hours * 3600))


async def send_review_requests(bot: Bot, db: Database, now: int, delay: int) -> None:
    markup = review_kb(await db.get_setting("review_yandex"), await db.get_setting("review_2gis"))
    text = await db.get_setting("review_text")
    for appt in await db.appointments_to_review(now, delay):
        await db.mark_review_sent(appt["id"])
        if not appt["chat_id"] or markup is None or not text:
            continue
        try:
            await bot.send_message(appt["chat_id"], text, reply_markup=markup)
        except Exception:
            log.warning("Не удалось попросить отзыв у клиента %s", appt["chat_id"])

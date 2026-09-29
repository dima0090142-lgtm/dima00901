import asyncio
import logging
import time
from html import escape

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .common import fmt_dt
from .config import Config
from .db import Database

log = logging.getLogger(__name__)


async def reminder_loop(bot: Bot, db: Database, cfg: Config) -> None:
    """Раз в минуту: напоминает клиентам о сеансе за сутки и спрашивает админа, пришёл ли клиент."""
    while True:
        try:
            await tick(bot, db, cfg)
        except Exception:
            log.exception("Ошибка в цикле напоминаний")
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

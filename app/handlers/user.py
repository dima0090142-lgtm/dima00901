from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from ..common import booking_kb
from ..config import Config
from ..db import Database

router = Router()


@router.message(CommandStart())
async def start(message: Message, db: Database, cfg: Config) -> None:
    user = message.from_user
    await db.upsert_user(user.id, user.first_name, user.username)
    text = (
        "<b>ТАТУ-КУЛЬТ</b>\n"
        "<i>Татуировка • Искусство • Культура</i>\n\n"
        "Здравствуйте! Здесь можно записаться на консультацию, посмотреть работы "
        "и найти ответы на частые вопросы.\n\n"
        "Нажмите кнопку ниже 👇"
    )
    if user.id in cfg.admin_ids:
        text += "\n\n🔑 Вы администратор — панель управления: /admin"
    await message.answer(text, reply_markup=booking_kb(cfg.webapp_url))


@router.message(Command("myid"))
async def my_id(message: Message) -> None:
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")


@router.message(F.chat.type == "private")
async def fallback(message: Message, cfg: Config) -> None:
    await message.answer(
        "Чтобы записаться или посмотреть работы, откройте приложение 👇",
        reply_markup=booking_kb(cfg.webapp_url),
    )

from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove

from ..common import booking_kb
from ..config import Config
from ..db import Database, format_phone

router = Router()

SHARE_PHONE_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="📱 Поделиться номером", request_contact=True)]],
    resize_keyboard=True,
    one_time_keyboard=True,
)


def welcome_text(cfg: Config, is_admin: bool) -> str:
    site = cfg.site_url.removeprefix("https://").removeprefix("http://").rstrip("/")
    text = (
        "<b>ТАТУ-КУЛЬТ</b>\n"
        "<i>Татуировка • Искусство • Культура</i>\n\n"
        "Здравствуйте! Здесь можно записаться на консультацию, посмотреть работы "
        "и найти ответы на частые вопросы.\n\n"
        f'🌐 Наш сайт: <a href="{cfg.site_url}">{site}</a>\n\n'
        "Нажмите кнопку ниже 👇"
    )
    if is_admin:
        text += "\n\n🔑 Вы администратор — панель управления: /admin"
    return text


async def notify_admins(bot: Bot, cfg: Config, text: str) -> None:
    for admin_id in cfg.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            pass


@router.message(CommandStart(deep_link=True, magic=F.args.startswith("c")))
async def start_invite(message: Message, command: CommandObject, db: Database, cfg: Config, bot: Bot) -> None:
    """Клиент пришёл по ссылке-приглашению из карточки клиента."""
    user = message.from_user
    await db.upsert_user(user.id, user.first_name, user.username)
    client = await db.client_by_invite(command.args[1:])
    if client and client["user_id"] in (None, user.id):
        if client["user_id"] is None:
            await db.link_client(client["id"], user.id)
            await notify_admins(
                bot, cfg,
                f"🔗 Клиент <b>{escape(client['name'])}</b> ({format_phone(client['phone'])}) подключился к боту.",
            )
        await message.answer(
            f"Готово, {escape(client['name'])}! 🖤\n\n"
            "Теперь мы пришлём сюда подтверждение записи, напоминание о сеансе и памятку по уходу.",
        )
    await message.answer(welcome_text(cfg, user.id in cfg.admin_ids), reply_markup=booking_kb(cfg.webapp_url))


@router.message(CommandStart())
async def start(message: Message, db: Database, cfg: Config) -> None:
    user = message.from_user
    await db.upsert_user(user.id, user.first_name, user.username)
    await message.answer(welcome_text(cfg, user.id in cfg.admin_ids), reply_markup=booking_kb(cfg.webapp_url))
    if user.id not in cfg.admin_ids and await db.client_by_user(user.id) is None:
        await message.answer(
            "Уже были у нас? Поделитесь номером — узнаем вас и будем присылать напоминания о сеансах.",
            reply_markup=SHARE_PHONE_KB,
        )


@router.message(F.contact)
async def shared_contact(message: Message, db: Database, cfg: Config, bot: Bot) -> None:
    """Клиент поделился своим номером — находим его в базе или добавляем."""
    contact = message.contact
    user = message.from_user
    if contact.user_id != user.id:
        await message.answer("Пожалуйста, поделитесь своим номером кнопкой ниже.", reply_markup=SHARE_PHONE_KB)
        return
    name = " ".join(filter(None, [contact.first_name, contact.last_name])) or user.first_name
    existing = await db.client_by_user(user.id)
    client = await db.upsert_client(contact.phone_number, name, user.id)
    await message.answer("Спасибо! Номер сохранён 🖤", reply_markup=ReplyKeyboardRemove())
    if client and existing is None and client["user_id"] == user.id:
        await notify_admins(
            bot, cfg, f"📱 <b>{escape(client['name'])}</b> ({format_phone(client['phone'])}) поделился номером в боте."
        )


@router.message(Command("myid"))
async def my_id(message: Message) -> None:
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")


@router.message(F.chat.type == "private")
async def fallback(message: Message, cfg: Config) -> None:
    await message.answer(
        "Чтобы записаться или посмотреть работы, откройте приложение 👇",
        reply_markup=booking_kb(cfg.webapp_url),
    )

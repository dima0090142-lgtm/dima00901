import asyncio
import logging
import time
import uuid
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, Filter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

from ..common import application_kb, application_text, fmt_dt, parse_dt
from ..config import Config
from ..db import Database

log = logging.getLogger(__name__)


class IsAdmin(Filter):
    async def __call__(self, event: Message | CallbackQuery, cfg: Config) -> bool:
        return event.from_user is not None and event.from_user.id in cfg.admin_ids


router = Router()
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


class AdminStates(StatesGroup):
    sched_time = State()
    bcast_msg = State()
    bcast_confirm = State()
    promo_text = State()
    faq_question = State()
    faq_answer = State()
    port_photos = State()
    edit_text = State()
    post_text = State()


TEXT_KEYS = {"about": "О салоне", "address": "Адрес", "contacts": "Контакты"}


def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


BACK = [btn("⬅️ В меню", "adm:menu")]

MENU_KB = kb(
    [btn("📋 Заявки", "adm:apps"), btn("📅 Сеансы", "adm:appts")],
    [btn("📣 Рассылка", "adm:bcast"), btn("🔥 Акции", "adm:promos")],
    [btn("❓ FAQ", "adm:faq"), btn("🖼 Портфолио", "adm:port")],
    [btn("✏️ Тексты", "adm:texts"), btn("📢 Пост в канал", "adm:post")],
)
MENU_TEXT = "<b>Панель администратора</b>\n\nВыберите раздел. Отменить любое действие: /cancel"


async def show(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Показывает экран, заменяя текущее сообщение (или присылает новое, если заменить нельзя)."""
    try:
        if callback.message.photo:
            raise TelegramBadRequest(method=None, message="photo")
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "not modified" in str(e):
            return
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            pass
        await callback.message.answer(text, reply_markup=markup)


# ---------- меню ----------

@router.message(Command("admin"))
async def admin_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(MENU_TEXT, reply_markup=MENU_KB)


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=MENU_KB)


@router.callback_query(F.data == "adm:menu")
async def menu_cb(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(callback, MENU_TEXT, MENU_KB)
    await callback.answer()


# ---------- заявки ----------

@router.callback_query(F.data == "adm:apps")
async def list_apps(callback: CallbackQuery, db: Database, cfg: Config) -> None:
    apps = await db.open_applications()
    if not apps:
        await show(callback, "Открытых заявок нет 🎉", kb(BACK))
    else:
        await show(callback, f"<b>Открытые заявки ({len(apps)})</b> — присылаю ниже 👇", kb(BACK))
        for app in apps:
            await callback.message.answer(application_text(app, cfg.tz), reply_markup=application_kb(app))
    await callback.answer()


@router.callback_query(F.data.startswith("app:contact:") | F.data.startswith("app:reject:"))
async def app_status(callback: CallbackQuery, db: Database, cfg: Config) -> None:
    _, action, app_id = callback.data.split(":")
    status = "contacted" if action == "contact" else "rejected"
    await db.set_application_status(int(app_id), status)
    app = await db.get_application(int(app_id))
    await callback.message.edit_text(application_text(app, cfg.tz), reply_markup=application_kb(app))
    await callback.answer("Готово")


@router.callback_query(F.data.startswith("app:sched:"))
async def sched_start(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    app_id = int(callback.data.split(":")[2])
    app = await db.get_application(app_id)
    await state.set_state(AdminStates.sched_time)
    await state.update_data(app_id=app_id)
    await callback.message.answer(
        f"Назначаем сеанс для <b>{escape(app['name'])}</b>.\n\n"
        "Напишите дату и время в формате <code>ДД.ММ ЧЧ:ММ</code>\n"
        "Например: <code>12.10 15:30</code>\n\nОтмена: /cancel"
    )
    await callback.answer()


@router.message(AdminStates.sched_time, F.text)
async def sched_finish(message: Message, state: FSMContext, db: Database, cfg: Config, bot: Bot) -> None:
    dt = parse_dt(message.text, cfg.tz)
    if dt is None:
        await message.answer("Не понял дату 🙈 Формат: <code>12.10 15:30</code>. Попробуйте ещё раз или /cancel")
        return
    starts_at = int(dt.timestamp())
    now = int(time.time())
    if starts_at <= now:
        await message.answer("Это время уже прошло. Введите дату в будущем или /cancel")
        return

    data = await state.get_data()
    app = await db.get_application(data["app_id"])
    await state.clear()
    # Если до сеанса меньше суток, отдельное напоминание не нужно — хватит подтверждения
    await db.add_appointment(app, starts_at, reminded=starts_at - now < 86400)
    await db.set_application_status(app["id"], "scheduled")

    when = fmt_dt(starts_at, cfg.tz)
    address = await db.get_setting("address") or ""
    try:
        await bot.send_message(
            app["user_id"],
            f"✅ <b>Вы записаны на сеанс!</b>\n\n"
            f"🗓 {when}\n📍 {escape(address)}\n👩‍🎨 Мастер: {escape(cfg.master_name)}\n\n"
            "Накануне пришлём напоминание. Если планы изменятся — пожалуйста, предупредите заранее.",
        )
        client_note = "Клиент получил уведомление в Telegram ✅"
    except (TelegramForbiddenError, TelegramBadRequest):
        client_note = (
            "⚠️ Не удалось написать клиенту в Telegram (он не разрешил сообщения от бота). "
            f"Сообщите ему сами: {escape(app['phone'])}"
        )
    await message.answer(
        f"📅 Сеанс назначен: <b>{escape(app['name'])}</b> — {when}\n{client_note}",
        reply_markup=MENU_KB,
    )


# ---------- сеансы ----------

@router.callback_query(F.data == "adm:appts")
async def list_appts(callback: CallbackQuery, db: Database, cfg: Config) -> None:
    appts = await db.upcoming_appointments()
    if not appts:
        await show(callback, "Запланированных сеансов нет.", kb(BACK))
    else:
        lines = ["<b>Ближайшие сеансы</b>\n"]
        rows = []
        for a in appts:
            lines.append(f"• {fmt_dt(a['starts_at'], cfg.tz)} — {escape(a['name'])}, {escape(a['phone'])}")
            rows.append([btn(f"🚫 Отменить: {a['name'][:20]}", f"appt:cancel:{a['id']}")])
        await show(callback, "\n".join(lines), kb(*rows, BACK))
    await callback.answer()


@router.callback_query(F.data.startswith("appt:cancel:"))
async def cancel_appt(callback: CallbackQuery, db: Database, cfg: Config) -> None:
    appt_id = int(callback.data.split(":")[2])
    await db.set_appointment_status(appt_id, "cancelled")
    await callback.answer("Сеанс отменён. Клиенту автоматически не сообщается.", show_alert=True)
    await list_appts(callback, db, cfg)


@router.callback_query(F.data.startswith("appt:came:") | F.data.startswith("appt:noshow:"))
async def arrival(callback: CallbackQuery, db: Database) -> None:
    _, action, appt_id = callback.data.split(":")
    came = action == "came"
    await db.set_appointment_status(int(appt_id), "came" if came else "no_show")
    note = "✅ Клиент пришёл" if came else "❌ Клиент не пришёл"
    await callback.message.edit_text(f"{callback.message.html_text}\n\n<b>{note}</b>")
    await callback.answer("Отмечено")


# ---------- рассылка ----------

@router.callback_query(F.data == "adm:bcast")
async def bcast_start(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    count = len(await db.active_user_ids())
    await state.set_state(AdminStates.bcast_msg)
    await show(
        callback,
        f"<b>Рассылка</b>\n\nПолучателей: {count}\n\n"
        "Пришлите сообщение для рассылки — текст, фото с подписью или видео. "
        "Я покажу, как оно выглядит, и спрошу подтверждение.",
        kb(BACK),
    )
    await callback.answer()


@router.message(AdminStates.bcast_msg)
async def bcast_preview(message: Message, state: FSMContext, db: Database) -> None:
    await state.update_data(chat_id=message.chat.id, message_id=message.message_id, text=None)
    await state.set_state(AdminStates.bcast_confirm)
    count = len(await db.active_user_ids())
    await message.answer(
        f"Отправить это сообщение {count} получателям?",
        reply_markup=kb([btn("✅ Отправить", "bcast:yes"), btn("✖️ Отмена", "adm:menu")]),
    )


@router.callback_query(AdminStates.bcast_confirm, F.data == "bcast:yes")
async def bcast_go(callback: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()
    await show(callback, "📣 Рассылка запущена. Сообщу, когда закончу.")
    await callback.answer()
    asyncio.create_task(run_broadcast(bot, db, callback.from_user.id, data))


async def run_broadcast(bot: Bot, db: Database, admin_id: int, data: dict) -> None:
    sent = failed = 0
    for user_id in await db.active_user_ids():
        for _ in range(2):
            try:
                if data.get("text"):
                    await bot.send_message(user_id, data["text"])
                else:
                    await bot.copy_message(user_id, data["chat_id"], data["message_id"])
                sent += 1
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except TelegramForbiddenError:
                await db.mark_blocked(user_id)
                failed += 1
                break
            except Exception:
                log.exception("Рассылка: ошибка для %s", user_id)
                failed += 1
                break
        await asyncio.sleep(0.05)
    await bot.send_message(
        admin_id, f"📣 Рассылка завершена.\nДоставлено: {sent}\nНе доставлено: {failed}", reply_markup=MENU_KB
    )


# ---------- акции ----------

@router.callback_query(F.data == "adm:promos")
async def promos(callback: CallbackQuery, db: Database) -> None:
    items = await db.list_promos()
    lines = ["<b>Акции и скидки</b>", "Показываются на главной странице приложения.\n"]
    rows = []
    for i, p in enumerate(items, 1):
        lines.append(f"<b>{i}.</b> {escape(p['text'][:200])}\n")
        rows.append([btn(f"🗑 Удалить акцию {i}", f"promo:del:{p['id']}")])
    if not items:
        lines.append("Пока акций нет.")
    await show(callback, "\n".join(lines), kb(*rows, [btn("➕ Добавить акцию", "promo:add")], BACK))
    await callback.answer()


@router.callback_query(F.data.startswith("promo:del:"))
async def promo_del(callback: CallbackQuery, db: Database) -> None:
    await db.delete_promo(int(callback.data.split(":")[2]))
    await promos(callback, db)


@router.callback_query(F.data == "promo:add")
async def promo_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.promo_text)
    await show(
        callback,
        "Пришлите текст акции.\nПервая строка станет заголовком, например:\n\n"
        "<code>−20% на первую татуировку\nДо конца октября при записи через Telegram</code>",
        kb(BACK),
    )
    await callback.answer()


@router.message(AdminStates.promo_text, F.text)
async def promo_save(message: Message, state: FSMContext, db: Database) -> None:
    text = message.text.strip()[:1000]
    await db.add_promo(text)
    await state.update_data(text=text, chat_id=None, message_id=None)
    await state.set_state(AdminStates.bcast_confirm)
    await message.answer(
        "✅ Акция добавлена в приложение.\n\nРазослать её всем подписчикам бота?",
        reply_markup=kb([btn("📣 Разослать", "bcast:yes"), btn("Не нужно", "adm:menu")]),
    )


# ---------- FAQ ----------

@router.callback_query(F.data == "adm:faq")
async def faq(callback: CallbackQuery, db: Database) -> None:
    items = await db.list_faq()
    lines = ["<b>Частые вопросы</b>\n"]
    rows = []
    for i, f in enumerate(items, 1):
        lines.append(f"<b>{i}. {escape(f['question'])}</b>\n{escape(f['answer'][:300])}\n")
        rows.append([btn(f"🗑 Удалить вопрос {i}", f"faq:del:{f['id']}")])
    if not items:
        lines.append("Вопросов пока нет.")
    await show(callback, "\n".join(lines)[:4000], kb(*rows, [btn("➕ Добавить вопрос", "faq:add")], BACK))
    await callback.answer()


@router.callback_query(F.data.startswith("faq:del:"))
async def faq_del(callback: CallbackQuery, db: Database) -> None:
    await db.delete_faq(int(callback.data.split(":")[2]))
    await faq(callback, db)


@router.callback_query(F.data == "faq:add")
async def faq_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.faq_question)
    await show(callback, "Напишите <b>вопрос</b>:", kb(BACK))
    await callback.answer()


@router.message(AdminStates.faq_question, F.text)
async def faq_question(message: Message, state: FSMContext) -> None:
    await state.update_data(question=message.text.strip()[:300])
    await state.set_state(AdminStates.faq_answer)
    await message.answer("Теперь напишите <b>ответ</b>:")


@router.message(AdminStates.faq_answer, F.text)
async def faq_answer(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    await db.add_faq(data["question"], message.text.strip()[:2000])
    await state.clear()
    await message.answer("✅ Вопрос добавлен.", reply_markup=kb([btn("❓ К списку FAQ", "adm:faq")], BACK))


# ---------- портфолио ----------

PORT_DONE_KB = kb([btn("✅ Готово", "adm:port")])


@router.callback_query(F.data == "adm:port")
async def portfolio(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    count = len(await db.list_portfolio())
    rows = [[btn("➕ Добавить фото", "port:add")]]
    if count:
        rows.append([btn("👀 Просмотр и удаление", "port:view:0")])
    await show(callback, f"<b>Портфолио</b>\n\nСейчас в галерее фото: {count}", kb(*rows, BACK))
    await callback.answer()


@router.callback_query(F.data == "port:add")
async def port_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.port_photos)
    await show(
        callback,
        "Присылайте фотографии работ (можно сразу несколько). "
        "Когда закончите — нажмите «Готово».",
        PORT_DONE_KB,
    )
    await callback.answer()


@router.message(AdminStates.port_photos, F.photo)
async def port_photo(message: Message, db: Database, cfg: Config, bot: Bot) -> None:
    filename = f"{uuid.uuid4().hex}.jpg"
    cfg.uploads_dir.mkdir(parents=True, exist_ok=True)
    await bot.download(message.photo[-1], destination=cfg.uploads_dir / filename)
    await db.add_portfolio(filename)
    count = len(await db.list_portfolio())
    await message.reply(f"Добавлено ✅ Всего фото: {count}", reply_markup=PORT_DONE_KB)


@router.message(AdminStates.port_photos)
async def port_not_photo(message: Message) -> None:
    await message.answer(
        "Пришлите именно фото (не файлом). Или нажмите «Готово».", reply_markup=PORT_DONE_KB
    )


@router.callback_query(F.data.startswith("port:view:"))
async def port_view(callback: CallbackQuery, db: Database, cfg: Config) -> None:
    await render_photo(callback, db, cfg, int(callback.data.split(":")[2]))


async def render_photo(callback: CallbackQuery, db: Database, cfg: Config, idx: int) -> None:
    photos = await db.list_portfolio()
    if not photos:
        await show(callback, "Галерея пуста.", kb([btn("⬅️ Назад", "adm:port")]))
        await callback.answer()
        return
    idx %= len(photos)
    photo = photos[idx]
    markup = kb(
        [
            btn("◀️", f"port:view:{idx - 1}"),
            btn("🗑 Удалить", f"port:del:{photo['id']}:{idx}"),
            btn("▶️", f"port:view:{idx + 1}"),
        ],
        [btn("⬅️ Назад", "adm:port")],
    )
    media = FSInputFile(cfg.uploads_dir / photo["filename"])
    caption = f"Фото {idx + 1} из {len(photos)}"
    if callback.message.photo:
        await callback.message.edit_media(InputMediaPhoto(media=media, caption=caption), reply_markup=markup)
    else:
        await callback.message.delete()
        await callback.message.answer_photo(media, caption=caption, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data.startswith("port:del:"))
async def port_del(callback: CallbackQuery, db: Database, cfg: Config) -> None:
    _, _, photo_id, idx = callback.data.split(":")
    photo = await db.fetchone("SELECT * FROM portfolio WHERE id = ?", int(photo_id))
    if photo:
        await db.delete_portfolio(photo["id"])
        (cfg.uploads_dir / photo["filename"]).unlink(missing_ok=True)
    await render_photo(callback, db, cfg, max(int(idx) - 1, 0))


# ---------- тексты ----------

@router.callback_query(F.data == "adm:texts")
async def texts(callback: CallbackQuery) -> None:
    await show(
        callback,
        "<b>Тексты в приложении</b>\n\nЧто хотите изменить?",
        kb(*[[btn(label, f"txt:{key}")] for key, label in TEXT_KEYS.items()], BACK),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("txt:"))
async def text_edit(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    key = callback.data.split(":")[1]
    current = await db.get_setting(key) or "—"
    await state.set_state(AdminStates.edit_text)
    await state.update_data(key=key)
    await show(
        callback,
        f"<b>{TEXT_KEYS[key]}</b> — сейчас так:\n\n{escape(current)}\n\nПришлите новый текст целиком:",
        kb([btn("⬅️ Назад", "adm:texts")]),
    )
    await callback.answer()


@router.message(AdminStates.edit_text, F.text)
async def text_save(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    await db.set_setting(data["key"], message.text.strip()[:3000])
    await state.clear()
    await message.answer("✅ Сохранено. В приложении уже обновилось.", reply_markup=MENU_KB)


# ---------- пост в канал ----------

DEFAULT_POST = (
    "<b>ТАТУ-КУЛЬТ</b>\n<i>Татуировка • Искусство • Культура</i>\n\n"
    "Теперь записаться на консультацию можно прямо в Telegram — "
    "нажмите кнопку ниже 👇"
)


@router.callback_query(F.data == "adm:post")
async def post_start(callback: CallbackQuery, state: FSMContext, cfg: Config) -> None:
    if not cfg.channel_id:
        await show(callback, "Канал не настроен: укажите CHANNEL_ID в настройках сервера.", kb(BACK))
        await callback.answer()
        return
    await state.set_state(AdminStates.post_text)
    await show(
        callback,
        "Опубликую в канале пост с кнопкой «Записаться».\n\n"
        "Пришлите текст поста или нажмите «Стандартный текст».",
        kb([btn("📝 Стандартный текст", "post:default")], BACK),
    )
    await callback.answer()


async def publish_post(bot: Bot, cfg: Config, text: str) -> str:
    link = cfg.miniapp_link or f"https://t.me/{(await bot.me()).username}?startapp=channel"
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✍️ Записаться на тату", url=link)]]
    )
    try:
        await bot.send_message(cfg.channel_id, text, reply_markup=markup)
        return "✅ Пост опубликован в канале. Его можно закрепить."
    except Exception as e:
        log.exception("Не удалось опубликовать пост")
        return (
            "⚠️ Не получилось опубликовать пост. Проверьте, что бот добавлен в канал "
            f"администратором с правом публикации.\n\nОшибка: <code>{escape(str(e))}</code>"
        )


@router.callback_query(AdminStates.post_text, F.data == "post:default")
async def post_default(callback: CallbackQuery, state: FSMContext, bot: Bot, cfg: Config) -> None:
    await state.clear()
    await show(callback, await publish_post(bot, cfg, DEFAULT_POST), MENU_KB)
    await callback.answer()


@router.message(AdminStates.post_text, F.text)
async def post_custom(message: Message, state: FSMContext, bot: Bot, cfg: Config) -> None:
    await state.clear()
    await message.answer(await publish_post(bot, cfg, message.html_text), reply_markup=MENU_KB)


@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def admin_hint(message: Message) -> None:
    await message.answer("Панель управления:", reply_markup=MENU_KB)

import re
from datetime import datetime
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ..common import fmt_dt
from ..config import Config
from ..db import Database, format_phone
from .admin import BACK, IsAdmin, btn, calendar_kb, calendar_text, kb, show

router = Router()
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

PAGE = 10
APPT_STATUS = {"scheduled": "📅", "came": "✅", "no_show": "❌", "cancelled": "🚫"}


class ClientStates(StatesGroup):
    add = State()
    search = State()
    note = State()
    rename = State()
    write = State()


CLIENTS_BACK = [btn("⬅️ Клиенты", "cl:menu")]


def client_label(c: dict) -> str:
    return f"{'✅' if c['started'] else '⚪'} {c['name'][:22]} · {format_phone(c['phone'])}"


def clients_kb(clients: list[dict], *extra: list) -> object:
    return kb(*[[btn(client_label(c), f"cl:c:{c['id']}")] for c in clients], *extra)


# ---------- меню ----------

@router.callback_query(F.data == "cl:menu")
async def clients_menu(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    total, linked = await db.count_clients()
    await show(
        callback,
        f"<b>👥 Клиенты</b>\n\nВсего: {total} · подключены к боту: {linked}\n\n"
        "✅ — клиент подключён к боту: получает подтверждения, напоминания, рассылки и памятку после сеанса.\n"
        "⚪ — ещё не подключён: откройте карточку и отправьте ему ссылку-приглашение.\n\n"
        "Клиенты из заявок добавляются сами. Чтобы добавить вручную — пришлите контакт "
        "(📎 → Контакт) или нажмите «Добавить».",
        kb(
            [btn("🔍 Найти по номеру или имени", "cl:find")],
            [btn("➕ Добавить клиента", "cl:add"), btn("📋 Все клиенты", "cl:all:0")],
            BACK,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cl:all:"))
async def all_clients(callback: CallbackQuery, db: Database) -> None:
    offset = int(callback.data.split(":")[2])
    total, _ = await db.count_clients()
    clients = await db.list_clients(PAGE, offset)
    nav = []
    if offset > 0:
        nav.append(btn("◀️", f"cl:all:{max(offset - PAGE, 0)}"))
    if offset + PAGE < total:
        nav.append(btn("▶️", f"cl:all:{offset + PAGE}"))
    text = f"<b>Все клиенты</b> ({offset + 1}–{min(offset + PAGE, total)} из {total})" if total else "Клиентов пока нет."
    await show(callback, text, clients_kb(clients, nav, CLIENTS_BACK))
    await callback.answer()


# ---------- поиск ----------

@router.callback_query(F.data == "cl:find")
async def find_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ClientStates.search)
    await show(
        callback,
        "Напишите номер телефона (можно только последние 4 цифры) или имя клиента:",
        kb(CLIENTS_BACK),
    )
    await callback.answer()


@router.message(ClientStates.search, F.text)
async def find_run(message: Message, state: FSMContext, db: Database) -> None:
    clients = await db.find_clients(message.text)
    if not clients:
        await message.answer(
            "Никого не нашёл 🤷 Напишите по-другому или добавьте клиента.",
            reply_markup=kb([btn("➕ Добавить клиента", "cl:add")], CLIENTS_BACK),
        )
        return
    await state.clear()
    await message.answer(
        f"Нашёл: {len(clients)}",
        reply_markup=clients_kb(clients, [btn("🔍 Искать ещё", "cl:find")], CLIENTS_BACK),
    )


# ---------- добавление ----------

@router.callback_query(F.data == "cl:add")
async def add_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ClientStates.add)
    await show(
        callback,
        "<b>Новый клиент</b>\n\n"
        "Пришлите <b>контакт</b> из Telegram (📎 → Контакт) — так клиент сразу привяжется к своему аккаунту.\n\n"
        "Или напишите имя и номер одной строкой, например:\n<code>Анна +7 999 123-45-67</code>",
        kb(CLIENTS_BACK),
    )
    await callback.answer()


@router.message(StateFilter(None, ClientStates.add), F.contact)
async def add_from_contact(message: Message, state: FSMContext, db: Database, cfg: Config, bot: Bot) -> None:
    contact = message.contact
    name = " ".join(filter(None, [contact.first_name, contact.last_name])) or "Без имени"
    await state.clear()
    await save_client(message, db, cfg, bot, contact.phone_number, name, contact.user_id)


@router.message(ClientStates.add, F.text)
async def add_from_text(message: Message, state: FSMContext, db: Database, cfg: Config, bot: Bot) -> None:
    match = re.search(r"\+?[\d\s\-()]{10,}", message.text)
    if not match:
        await message.answer("Не вижу номера 🙈 Пример: <code>Анна +7 999 123-45-67</code>")
        return
    name = (message.text[:match.start()] + message.text[match.end():]).strip(" ,-") or "Без имени"
    await state.clear()
    await save_client(message, db, cfg, bot, match.group(), name, None)


async def save_client(
    message: Message, db: Database, cfg: Config, bot: Bot, phone: str, name: str, user_id: int | None
) -> None:
    before = await db.find_clients(phone, limit=1) if len(re.sub(r"\D", "", phone)) >= 10 else []
    client = await db.upsert_client(phone, name, user_id)
    if client is None:
        await message.answer("Номер слишком короткий — проверьте его и пришлите ещё раз.")
        return
    note = "Такой клиент уже был в базе 👇" if before else "✅ Клиент добавлен"
    await message.answer(note)
    await send_card(message, db, cfg, bot, client["id"])


# ---------- карточка ----------

async def card(db: Database, cfg: Config, bot: Bot, client_id: int) -> tuple[str, object] | None:
    c = await db.get_client(client_id)
    if c is None:
        return None
    appts = await db.client_appointments(client_id)
    apps_count = await db.client_applications_count(client_id)
    lines = [
        f"👤 <b>{escape(c['name'])}</b>",
        f"📱 {format_phone(c['phone'])}",
        "Telegram: ✅ подключён к боту" if c["started"] else "Telegram: ⚪ не подключён — отправьте ссылку-приглашение",
        f"Заявок: {apps_count}",
    ]
    if c["note"]:
        lines.append(f"\n📝 {escape(c['note'])}")
    if appts:
        lines.append("\n<b>Сеансы:</b>")
        lines += [f"{APPT_STATUS.get(a['status'], '•')} {fmt_dt(a['starts_at'], cfg.tz)}" for a in appts]
    lines.append(f"\n<i>Добавлен {datetime.fromtimestamp(c['created_at'], cfg.tz):%d.%m.%Y}</i>")

    rows = [[btn("📅 Назначить сеанс", f"cl:book:{client_id}")]]
    if c["started"]:
        rows[0].append(btn("✉️ Написать", f"cl:write:{client_id}"))
    else:
        rows.append([btn("🔗 Ссылка-приглашение", f"cl:inv:{client_id}")])
    rows.append([btn("📝 Заметка", f"cl:note:{client_id}"), btn("✏️ Имя", f"cl:rename:{client_id}")])
    rows.append([btn("🗑 Удалить", f"cl:del:{client_id}")])
    rows.append(CLIENTS_BACK)
    return "\n".join(lines), kb(*rows)


async def send_card(message: Message, db: Database, cfg: Config, bot: Bot, client_id: int) -> None:
    result = await card(db, cfg, bot, client_id)
    if result:
        await message.answer(result[0], reply_markup=result[1])


@router.callback_query(F.data.startswith("cl:c:"))
async def open_card(callback: CallbackQuery, state: FSMContext, db: Database, cfg: Config, bot: Bot) -> None:
    await state.clear()
    result = await card(db, cfg, bot, int(callback.data.split(":")[2]))
    if result is None:
        await callback.answer("Клиент не найден", show_alert=True)
        return
    await show(callback, *result)
    await callback.answer()


@router.callback_query(F.data.startswith("cl:inv:"))
async def invite(callback: CallbackQuery, db: Database, bot: Bot) -> None:
    c = await db.get_client(int(callback.data.split(":")[2]))
    link = f"https://t.me/{(await bot.me()).username}?start=c{c['invite_code']}"
    await callback.message.answer("Перешлите клиенту сообщение ниже 👇 Когда он нажмёт ссылку, я сообщу.")
    await callback.message.answer(
        f"Здравствуйте, {escape(c['name'])}! Это тату-студия «Тату-Культ» 🖤\n\n"
        "Подключитесь к нашему боту — пришлём подтверждение записи, напоминание о сеансе "
        f"и памятку по уходу:\n{link}"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cl:book:"))
async def book_client(callback: CallbackQuery, db: Database, cfg: Config) -> None:
    c = await db.get_client(int(callback.data.split(":")[2]))
    # Служебная заявка: через неё работает тот же календарь, что и для обычных заявок
    app_id = await db.add_application(
        c["user_id"] or 0, c["name"], format_phone(c["phone"]), "Запись через админку", c["id"], status="admin", source="bot"
    )
    app = await db.get_application(app_id)
    now = datetime.now(cfg.tz)
    await callback.message.answer(
        calendar_text(app), reply_markup=await calendar_kb(db, cfg, app_id, now.year, now.month)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cl:del:"))
async def delete_ask(callback: CallbackQuery, db: Database) -> None:
    client_id = int(callback.data.split(":")[2])
    c = await db.get_client(client_id)
    await show(
        callback,
        f"Удалить клиента <b>{escape(c['name'])}</b> из базы? Его заявки и сеансы останутся.",
        kb([btn("🗑 Да, удалить", f"cl:delok:{client_id}"), btn("Отмена", f"cl:c:{client_id}")]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cl:delok:"))
async def delete_ok(callback: CallbackQuery, db: Database) -> None:
    await db.delete_client(int(callback.data.split(":")[2]))
    await show(callback, "Клиент удалён.", kb(CLIENTS_BACK))
    await callback.answer()


# ---------- заметка, имя, сообщение ----------

@router.callback_query(F.data.startswith("cl:note:") | F.data.startswith("cl:rename:") | F.data.startswith("cl:write:"))
async def edit_start(callback: CallbackQuery, state: FSMContext) -> None:
    _, action, client_id = callback.data.split(":")
    states = {
        "note": (ClientStates.note, "Напишите заметку о клиенте (увидите только вы). «-» — очистить."),
        "rename": (ClientStates.rename, "Напишите новое имя клиента:"),
        "write": (ClientStates.write, "Пришлите сообщение для клиента — текст, фото или видео. Я перешлю его от имени бота."),
    }
    new_state, prompt = states[action]
    await state.set_state(new_state)
    await state.update_data(client_id=int(client_id))
    await callback.message.answer(f"{prompt}\n\nОтмена: /cancel")
    await callback.answer()


@router.message(ClientStates.note, F.text)
async def note_save(message: Message, state: FSMContext, db: Database, cfg: Config, bot: Bot) -> None:
    client_id = (await state.get_data())["client_id"]
    await state.clear()
    await db.set_client_note(client_id, "" if message.text.strip() == "-" else message.text.strip()[:1000])
    await send_card(message, db, cfg, bot, client_id)


@router.message(ClientStates.rename, F.text)
async def rename_save(message: Message, state: FSMContext, db: Database, cfg: Config, bot: Bot) -> None:
    client_id = (await state.get_data())["client_id"]
    await state.clear()
    await db.set_client_name(client_id, message.text.strip()[:100])
    await send_card(message, db, cfg, bot, client_id)


@router.message(ClientStates.write)
async def write_send(message: Message, state: FSMContext, db: Database, cfg: Config, bot: Bot) -> None:
    client_id = (await state.get_data())["client_id"]
    await state.clear()
    c = await db.get_client(client_id)
    try:
        await bot.copy_message(c["user_id"], message.chat.id, message.message_id)
        await message.answer("✅ Отправлено клиенту.")
    except Exception:
        await message.answer("⚠️ Не удалось отправить: клиент ещё не нажал «Старт» в боте или заблокировал его.")
    await send_card(message, db, cfg, bot, client_id)

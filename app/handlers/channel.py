"""Канал: посты через бота, расписание, ИИ-помощник и напоминания «пора что-то выложить»."""
import logging
import re
import time
from datetime import datetime, timedelta
from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..ai import AIError, ContentAI, random_ideas
from ..common import fmt_dt, parse_dt
from ..config import Config
from ..db import Database
from .admin import IsAdmin, btn, kb, show

log = logging.getLogger(__name__)

router = Router()
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

# Отдельный роутер без фильтра админа: видит посты, которые публикуют в канале вручную
channel_watch = Router()

REMIND_HOUR = 11  # во сколько (по местному времени) присылать напоминание
MARKETING_BACK = [btn("⬅️ Продвижение", "adm:mkt")]
CHANNEL_BACK = [btn("⬅️ Канал", "ch:menu")]


class PostStates(StatesGroup):
    content = State()
    ai_brief = State()
    own_text = State()
    schedule_time = State()
    channel = State()


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


async def channel_id(db: Database, cfg: Config) -> str:
    return await db.get_setting("channel_id") or cfg.channel_id


async def booking_markup(bot: Bot, cfg: Config) -> InlineKeyboardMarkup:
    link = cfg.miniapp_link or f"https://t.me/{(await bot.me()).username}?startapp=channel"
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✍️ Записаться на тату", url=link)]])


# ---------- публикация ----------

async def publish(bot: Bot, db: Database, cfg: Config, post: dict) -> str | None:
    """Публикует пост в канал. Возвращает текст ошибки или None, если всё хорошо."""
    target = await channel_id(db, cfg)
    if not target:
        return "Канал не указан: «📢 Канал» → «⚙️ Канал и напоминания»."
    try:
        markup = await booking_markup(bot, cfg) if post["with_button"] else None
        await bot.copy_message(target, post["src_chat"], post["src_message"], reply_markup=markup)
    except Exception as e:
        log.warning("Не удалось опубликовать пост %s: %s", post["id"], e)
        return (
            "Не получилось опубликовать. Проверьте, что бот — администратор канала с правом публикации.\n"
            f"<code>{escape(str(e))[:200]}</code>"
        )
    await db.update_post(post["id"], status="published", published_at=int(time.time()))
    return None


async def publish_due(bot: Bot, db: Database, cfg: Config) -> None:
    """Вызывается раз в минуту: публикует запланированные посты."""
    for post in await db.due_posts(int(time.time())):
        error = await publish(bot, db, cfg, post)
        if error:
            await db.update_post(post["id"], status="draft")
        note = f"📤 Запланированный пост №{post['id']} опубликован." if not error else f"⚠️ Пост №{post['id']}: {error}"
        for admin_id in cfg.admin_ids:
            try:
                await bot.send_message(admin_id, note)
            except Exception:
                pass


# ---------- меню ----------

@router.callback_query(F.data == "ch:menu")
async def channel_menu(callback: CallbackQuery, state: FSMContext, db: Database, cfg: Config, ai: ContentAI) -> None:
    await state.clear()
    target = await channel_id(db, cfg)
    title = await db.get_setting("channel_title") or target or "не указан"
    last = await db.last_published_at()
    if last:
        days = int((time.time() - last) // 86400)
        last_text = "сегодня" if days == 0 else f"{days} {plural(days, 'день', 'дня', 'дней')} назад"
    else:
        last_text = "ещё не было"
    scheduled = len(await db.scheduled_posts())
    interval = int(await db.get_setting("post_interval_days") or 3)
    lines = [
        "<b>📢 Канал</b>",
        f"\nКанал: <b>{escape(title)}</b>",
        f"Последний пост: {last_text}",
        f"Запланировано: {scheduled}",
        f"Напоминания: {'каждые ' + str(interval) + ' ' + plural(interval, 'день', 'дня', 'дней') + ' без постов' if interval else 'выключены'}",
        f"ИИ-помощник: {'✅ подключён' if ai.enabled else '⚪ не подключён (идеи из готового списка)'}",
    ]
    rows = [[btn("✍️ Новый пост", "ch:new")]]
    if ai.enabled:
        rows[0].append(btn("✨ Пост с ИИ", "ch:ai"))
    rows += [
        [btn("💡 Идеи для постов", "ch:ideas")],
        [btn(f"🗓 Запланированные ({scheduled})", "ch:sched")],
        [btn("⚙️ Канал и напоминания", "ch:set")],
        MARKETING_BACK,
    ]
    await show(callback, "\n".join(lines), kb(*rows))
    await callback.answer()


# ---------- черновик ----------

def draft_kb(post: dict, ai_enabled: bool) -> InlineKeyboardMarkup:
    pid = post["id"]
    rows = [
        [btn("📤 Опубликовать сейчас", f"pst:pub:{pid}")],
        [btn("🕒 Запланировать", f"pst:when:{pid}")],
        [btn(("✅" if post["with_button"] else "⬜") + " Кнопка «Записаться»", f"pst:btn:{pid}")],
    ]
    edit_row = []
    if ai_enabled and post["kind"] in ("text", "photo") and (post["ai_brief"] is not None or post["text"]):
        edit_row.append(btn("✨ Другой вариант" if post["ai_brief"] is not None else "✨ Улучшить ИИ", f"pst:ai:{pid}"))
    if post["kind"] in ("text", "photo"):
        edit_row.append(btn("✏️ Свой текст", f"pst:edit:{pid}"))
    if edit_row:
        rows.append(edit_row)
    rows.append([btn("🗑 Удалить черновик", f"pst:del:{pid}")])
    return kb(*rows)


async def send_control(bot: Bot, chat_id: int, post: dict, ai: ContentAI) -> None:
    await bot.send_message(
        chat_id,
        f"👆 <b>Черновик поста №{post['id']}</b> — так он будет выглядеть в канале.",
        reply_markup=draft_kb(post, ai.enabled),
    )


async def send_content(bot: Bot, chat_id: int, text: str, photo_file_id: str | None) -> Message:
    """Отправляет админу сам пост — именно это сообщение потом копируется в канал."""
    if photo_file_id:
        return await bot.send_photo(chat_id, photo_file_id, caption=text[:1024] or None, parse_mode=None)
    return await bot.send_message(chat_id, text[:4096], parse_mode=None)


@router.callback_query(F.data == "ch:new")
async def new_post(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PostStates.content)
    await show(
        callback,
        "<b>✍️ Новый пост</b>\n\nПришлите пост одним сообщением: текст, фото с подписью или видео. "
        "Можно переслать сообщение из другого чата.",
        kb(CHANNEL_BACK),
    )
    await callback.answer()


@router.message(PostStates.content)
async def new_post_content(message: Message, state: FSMContext, db: Database, bot: Bot, ai: ContentAI) -> None:
    await state.clear()
    if message.photo:
        kind, photo, text = "photo", message.photo[-1].file_id, message.caption or ""
    elif message.text:
        kind, photo, text = "text", None, message.text
    else:
        kind, photo, text = "media", None, message.caption or ""
    post_id = await db.add_post(message.chat.id, message.message_id, kind=kind, text=text, photo_file_id=photo)
    if message.media_group_id:
        await message.answer("ℹ️ Альбомы публикуются по одному фото — в черновик попало только это.")
    await send_control(bot, message.chat.id, await db.get_post(post_id), ai)


@router.callback_query(F.data.startswith("pst:btn:"))
async def toggle_button(callback: CallbackQuery, db: Database, ai: ContentAI) -> None:
    post = await db.get_post(int(callback.data.split(":")[2]))
    await db.update_post(post["id"], with_button=0 if post["with_button"] else 1)
    await callback.message.edit_reply_markup(reply_markup=draft_kb(await db.get_post(post["id"]), ai.enabled))
    await callback.answer()


@router.callback_query(F.data.startswith("pst:pub:"))
async def publish_now(callback: CallbackQuery, db: Database, cfg: Config, bot: Bot) -> None:
    post = await db.get_post(int(callback.data.split(":")[2]))
    if post is None or post["status"] == "published":
        await callback.answer("Этот пост уже опубликован", show_alert=True)
        return
    error = await publish(bot, db, cfg, post)
    if error:
        await callback.message.answer(f"⚠️ {error}")
        await callback.answer()
        return
    await callback.message.edit_text(f"✅ Пост №{post['id']} опубликован в канале.", reply_markup=kb(CHANNEL_BACK))
    await callback.answer("Опубликовано")


@router.callback_query(F.data.startswith("pst:del:"))
async def delete_draft(callback: CallbackQuery, db: Database) -> None:
    post_id = int(callback.data.split(":")[2])
    await db.update_post(post_id, status="cancelled")
    await callback.message.edit_text(f"🗑 Черновик №{post_id} удалён.", reply_markup=kb(CHANNEL_BACK))
    await callback.answer()


# ---------- расписание ----------

@router.callback_query(F.data.startswith("pst:when:"))
async def schedule_options(callback: CallbackQuery, cfg: Config) -> None:
    post_id = int(callback.data.split(":")[2])
    now = datetime.now(cfg.tz)
    tomorrow = now + timedelta(days=1)
    options = []
    for day, hour, label in [(now, 12, "Сегодня 12:00"), (now, 19, "Сегодня 19:00"),
                             (tomorrow, 12, "Завтра 12:00"), (tomorrow, 19, "Завтра 19:00")]:
        slot = day.replace(hour=hour, minute=0, second=0, microsecond=0)
        if slot > now + timedelta(minutes=5):
            options.append(btn(label, f"pst:at:{post_id}:{slot:%Y%m%d%H%M}"))
    rows = [options[i:i + 2] for i in range(0, len(options), 2)]
    rows.append([btn("✏️ Своё время", f"pst:time:{post_id}")])
    rows.append([btn("⬅️ Назад", f"pst:back:{post_id}")])
    await callback.message.edit_text(f"🕒 Когда опубликовать пост №{post_id}?", reply_markup=kb(*rows))
    await callback.answer()


@router.callback_query(F.data.startswith("pst:back:"))
async def schedule_back(callback: CallbackQuery, db: Database, ai: ContentAI) -> None:
    post = await db.get_post(int(callback.data.split(":")[2]))
    await callback.message.edit_text(
        f"👆 <b>Черновик поста №{post['id']}</b> — так он будет выглядеть в канале.",
        reply_markup=draft_kb(post, ai.enabled),
    )
    await callback.answer()


async def set_schedule(db: Database, cfg: Config, post_id: int, dt: datetime) -> str:
    await db.update_post(post_id, status="scheduled", publish_at=int(dt.timestamp()))
    return f"🕒 Пост №{post_id} запланирован на <b>{fmt_dt(int(dt.timestamp()), cfg.tz)}</b>."


def scheduled_kb(post_id: int) -> InlineKeyboardMarkup:
    return kb(
        [btn("📤 Опубликовать сейчас", f"pst:pub:{post_id}"), btn("🚫 Отменить", f"pst:unsch:{post_id}")],
        CHANNEL_BACK,
    )


@router.callback_query(F.data.startswith("pst:at:"))
async def schedule_pick(callback: CallbackQuery, db: Database, cfg: Config) -> None:
    _, _, post_id, stamp = callback.data.split(":")
    dt = datetime.strptime(stamp, "%Y%m%d%H%M").replace(tzinfo=cfg.tz)
    text = await set_schedule(db, cfg, int(post_id), dt)
    await callback.message.edit_text(text, reply_markup=scheduled_kb(int(post_id)))
    await callback.answer("Запланировано")


@router.callback_query(F.data.startswith("pst:time:"))
async def schedule_custom(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PostStates.schedule_time)
    await state.update_data(post_id=int(callback.data.split(":")[2]))
    await callback.message.answer(
        "Напишите дату и время: <code>12.10 18:30</code>, или только время на сегодня: <code>18:30</code>\n\nОтмена: /cancel"
    )
    await callback.answer()


@router.message(PostStates.schedule_time, F.text)
async def schedule_custom_save(message: Message, state: FSMContext, db: Database, cfg: Config) -> None:
    text = message.text.strip()
    if re.fullmatch(r"\d{1,2}[:.]\d{2}", text):
        text = f"{datetime.now(cfg.tz):%d.%m.%Y} {text}"
    dt = parse_dt(text, cfg.tz)
    if dt is None or dt <= datetime.now(cfg.tz):
        await message.answer("Не понял время или оно уже прошло. Пример: <code>12.10 18:30</code> или /cancel")
        return
    post_id = (await state.get_data())["post_id"]
    await state.clear()
    await message.answer(await set_schedule(db, cfg, post_id, dt), reply_markup=scheduled_kb(post_id))


@router.callback_query(F.data.startswith("pst:unsch:"))
async def unschedule(callback: CallbackQuery, db: Database, ai: ContentAI) -> None:
    post_id = int(callback.data.split(":")[2])
    await db.update_post(post_id, status="draft", publish_at=None)
    post = await db.get_post(post_id)
    await callback.message.edit_text(
        f"Публикация поста №{post_id} отменена — он снова черновик.", reply_markup=draft_kb(post, ai.enabled)
    )
    await callback.answer()


@router.callback_query(F.data == "ch:sched")
async def scheduled_list(callback: CallbackQuery, db: Database, cfg: Config) -> None:
    posts = await db.scheduled_posts()
    if not posts:
        await show(callback, "Запланированных постов нет.", kb(CHANNEL_BACK))
        await callback.answer()
        return
    lines = ["<b>🗓 Запланированные посты</b>\n"]
    rows = []
    for p in posts:
        preview = (p["text"] or "(без текста)").replace("\n", " ")[:50]
        lines.append(f"№{p['id']} · {fmt_dt(p['publish_at'], cfg.tz)}\n<i>{escape(preview)}</i>\n")
        rows.append([btn(f"📤 Сейчас №{p['id']}", f"pst:pub:{p['id']}"), btn(f"🚫 Отменить №{p['id']}", f"pst:unsch:{p['id']}")])
    await show(callback, "\n".join(lines), kb(*rows, CHANNEL_BACK))
    await callback.answer()


# ---------- ИИ ----------

@router.callback_query(F.data == "ch:ai")
async def ai_post_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PostStates.ai_brief)
    await state.update_data(brief=None)
    await show(
        callback,
        "<b>✨ Пост с ИИ</b>\n\nПришлите <b>фото работы</b> — можно с подписью, о чём рассказать. "
        "Или просто напишите тему, например: <i>свободные окна на следующей неделе</i>.",
        kb(CHANNEL_BACK),
    )
    await callback.answer()


async def make_ai_draft(message: Message, db: Database, bot: Bot, ai: ContentAI,
                        brief: str, photo_file_id: str | None, replace_post: int | None = None) -> None:
    status = await message.answer("✍️ Пишу пост…")
    photo = None
    if photo_file_id:
        photo = (await bot.download(photo_file_id)).read()
    try:
        text = await ai.write_post(brief, photo)
    except AIError as e:
        await status.edit_text(f"⚠️ {e}")
        return
    await status.delete()
    content = await send_content(bot, message.chat.id, text, photo_file_id)
    kind = "photo" if photo_file_id else "text"
    if replace_post:
        await db.update_post(replace_post, src_chat=content.chat.id, src_message=content.message_id, text=text)
        post_id = replace_post
    else:
        post_id = await db.add_post(content.chat.id, content.message_id, kind=kind, text=text,
                                    photo_file_id=photo_file_id, ai_brief=brief)
    await send_control(bot, message.chat.id, await db.get_post(post_id), ai)


@router.message(PostStates.ai_brief, F.photo)
async def ai_post_photo(message: Message, state: FSMContext, db: Database, bot: Bot, ai: ContentAI) -> None:
    preset = (await state.get_data()).get("brief")
    await state.clear()
    brief = "\n".join(filter(None, [preset, message.caption]))
    await make_ai_draft(message, db, bot, ai, brief, message.photo[-1].file_id)


@router.message(PostStates.ai_brief, F.text)
async def ai_post_text(message: Message, state: FSMContext, db: Database, bot: Bot, ai: ContentAI) -> None:
    preset = (await state.get_data()).get("brief")
    await state.clear()
    brief = preset if message.text.strip().lower() in ("без фото", "нет") and preset else message.text
    await make_ai_draft(message, db, bot, ai, brief, None)


@router.callback_query(F.data.startswith("pst:ai:"))
async def ai_regenerate(callback: CallbackQuery, db: Database, bot: Bot, ai: ContentAI) -> None:
    post = await db.get_post(int(callback.data.split(":")[2]))
    brief = post["ai_brief"] if post["ai_brief"] is not None else (
        f"Улучши этот текст поста, сохранив все факты и смысл:\n{post['text']}"
    )
    if post["ai_brief"] is None:
        await db.update_post(post["id"], ai_brief=brief)
    await callback.message.edit_text(f"Черновик №{post['id']}: готовлю новый вариант ниже 👇")
    await callback.answer()
    await make_ai_draft(callback.message, db, bot, ai, brief, post["photo_file_id"], replace_post=post["id"])


@router.callback_query(F.data.startswith("pst:edit:"))
async def own_text_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PostStates.own_text)
    await state.update_data(post_id=int(callback.data.split(":")[2]))
    await callback.message.answer("Пришлите новый текст поста целиком. Отмена: /cancel")
    await callback.answer()


@router.message(PostStates.own_text, F.text)
async def own_text_save(message: Message, state: FSMContext, db: Database, bot: Bot, ai: ContentAI) -> None:
    post_id = (await state.get_data())["post_id"]
    await state.clear()
    post = await db.get_post(post_id)
    content = await send_content(bot, message.chat.id, message.text, post["photo_file_id"])
    await db.update_post(post_id, src_chat=content.chat.id, src_message=content.message_id, text=message.text)
    await send_control(bot, message.chat.id, await db.get_post(post_id), ai)


@router.callback_query(F.data == "ch:ideas")
async def ideas(callback: CallbackQuery, state: FSMContext, ai: ContentAI) -> None:
    await callback.answer()
    note = ""
    if ai.enabled:
        wait = await callback.message.answer("💡 Придумываю идеи…")
        try:
            items = await ai.ideas()
        except AIError as e:
            items, note = random_ideas(), f"\n\n<i>ИИ не ответил ({e}), показываю идеи из списка.</i>"
        await wait.delete()
    else:
        items = random_ideas()
    await state.update_data(ideas=items)
    lines = ["<b>💡 Идеи для постов</b>\n"] + [f"{i}. {escape(t)}" for i, t in enumerate(items, 1)]
    rows = []
    if ai.enabled:
        rows = [[btn(f"✨ Пост по идее {i}", f"ch:idea:{i - 1}") for i in range(1, len(items) + 1)][j:j + 3]
                for j in range(0, len(items), 3)]
    rows += [[btn("🔄 Другие идеи", "ch:ideas")], CHANNEL_BACK]
    await callback.message.answer("\n".join(lines) + note, reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("ch:idea:"))
async def idea_to_post(callback: CallbackQuery, state: FSMContext) -> None:
    items = (await state.get_data()).get("ideas") or []
    idx = int(callback.data.split(":")[2])
    if idx >= len(items):
        await callback.answer("Идеи устарели — откройте список заново", show_alert=True)
        return
    await state.set_state(PostStates.ai_brief)
    await state.update_data(brief=f"Тема поста: {items[idx]}")
    await callback.message.answer(
        f"Тема: <i>{escape(items[idx])}</i>\n\nПришлите фото для поста или напишите «без фото».",
    )
    await callback.answer()


# ---------- настройки канала и напоминаний ----------

@router.callback_query(F.data == "ch:set")
async def channel_settings(callback: CallbackQuery, state: FSMContext, db: Database, cfg: Config) -> None:
    await state.clear()
    target = await channel_id(db, cfg)
    title = await db.get_setting("channel_title") or target or "не указан"
    interval = int(await db.get_setting("post_interval_days") or 3)
    rows = [[btn("🔄 Сменить канал", "ch:chan")]]
    rows.append([btn(("✅ " if interval == d else "") + f"{d} дн.", f"ch:int:{d}") for d in (2, 3, 5, 7)])
    rows.append([btn(("✅ " if interval == 0 else "") + "🔕 Не напоминать", "ch:int:0")])
    rows.append(CHANNEL_BACK)
    await show(
        callback,
        f"<b>⚙️ Канал и напоминания</b>\n\nКанал: <b>{escape(title)}</b>\n\n"
        f"Если в канале нет постов столько дней, около {REMIND_HOUR}:00 я напомню и предложу идеи. "
        "По понедельникам спрошу, что интересного было на неделе.",
        kb(*rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ch:int:"))
async def set_interval(callback: CallbackQuery, state: FSMContext, db: Database, cfg: Config) -> None:
    await db.set_setting("post_interval_days", callback.data.split(":")[2])
    await channel_settings(callback, state, db, cfg)


@router.callback_query(F.data == "ch:chan")
async def change_channel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PostStates.channel)
    await callback.message.answer(
        "Сначала добавьте бота в канал <b>администратором</b> с правом публикации.\n\n"
        "Затем пришлите сюда @имя канала (например <code>@tattookult</code>) "
        "или перешлите любое сообщение из этого канала.\n\nОтмена: /cancel"
    )
    await callback.answer()


@router.message(PostStates.channel)
async def change_channel_save(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    origin = getattr(message, "forward_origin", None)
    if origin is not None and getattr(origin, "chat", None) is not None:
        ref = origin.chat.id
    elif message.text and re.fullmatch(r"@?[A-Za-z0-9_]{4,}|-100\d+", message.text.strip()):
        ref = message.text.strip()
        if not ref.startswith(("@", "-")):
            ref = "@" + ref
    else:
        await message.answer("Пришлите @имя канала или перешлите сообщение из канала. Отмена: /cancel")
        return
    try:
        chat = await bot.get_chat(ref)
        member = await bot.get_chat_member(chat.id, (await bot.me()).id)
    except Exception:
        await message.answer("Не нашёл такой канал или бот в нём не состоит. Проверьте и пришлите ещё раз.")
        return
    can_post = member.status == "creator" or (
        member.status == "administrator" and (chat.type != "channel" or getattr(member, "can_post_messages", False))
    )
    if not can_post:
        await message.answer("Бот есть в канале, но не может публиковать. Сделайте его администратором с правом публикации.")
        return
    await state.clear()
    await db.set_setting("channel_id", str(chat.id))
    await db.set_setting("channel_title", chat.title or str(chat.id))
    await message.answer(f"✅ Теперь посты уходят в «{escape(chat.title or str(chat.id))}».", reply_markup=kb(CHANNEL_BACK))


# ---------- напоминания ----------

@router.callback_query(F.data == "ch:snooze")
async def snooze(callback: CallbackQuery, db: Database) -> None:
    await db.set_setting("post_snooze_until", str(int(time.time()) + 7 * 86400))
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Хорошо, неделю не напоминаю", show_alert=True)


async def reminder_tick(bot: Bot, db: Database, cfg: Config, ai: ContentAI) -> None:
    """Раз в день около REMIND_HOUR: «пора что-то выложить» или вопрос по понедельникам."""
    now = datetime.now(cfg.tz)
    today = now.date().isoformat()
    if now.hour < REMIND_HOUR or await db.get_setting("post_reminder_day") == today:
        return
    if not await channel_id(db, cfg):
        return
    interval = int(await db.get_setting("post_interval_days") or 3)
    if interval == 0 or int(await db.get_setting("post_snooze_until") or 0) > time.time():
        return
    if await db.scheduled_posts():
        return  # пост уже запланирован — не дёргаем
    last = await db.last_published_at()
    days = int((time.time() - last) // 86400) if last else None
    overdue = days is None or days >= interval
    if not overdue and now.weekday() != 0:
        return
    await db.set_setting("post_reminder_day", today)

    if overdue:
        head = ("📅 В канале ещё не было постов через бота." if days is None
                else f"📅 В канале уже {days} {plural(days, 'день', 'дня', 'дней')} без новых постов.")
        text = f"{head} Что выложим?"
    else:
        text = "☀️ Новая неделя! Были интересные работы или новости? Пришлите фото — помогу сделать пост."
    text += "\n\nМожно, например:\n" + "\n".join(f"• {escape(i)}" for i in random_ideas(3))
    rows = [[btn("✍️ Новый пост", "ch:new")]]
    if ai.enabled:
        rows[0].append(btn("✨ Пост с ИИ", "ch:ai"))
    rows += [[btn("💡 Ещё идеи", "ch:ideas")], [btn("🔕 Неделю не напоминать", "ch:snooze")]]
    for admin_id in cfg.admin_ids:
        try:
            await bot.send_message(admin_id, text, reply_markup=kb(*rows))
        except Exception:
            pass


# ---------- посты, опубликованные в канале вручную ----------

@channel_watch.channel_post()
async def channel_post_seen(message: Message, db: Database, cfg: Config) -> None:
    target = await channel_id(db, cfg)
    matches = str(message.chat.id) == target or (
        message.chat.username and target.lower() == "@" + message.chat.username.lower()
    )
    if matches:
        await db.add_post(None, None, kind="photo" if message.photo else "text",
                          text=message.text or message.caption or "", status="published", source="channel")

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat, MenuButtonWebApp, WebAppInfo
from aiohttp import web

from app import VERSION
from app.config import Config
from app.db import Database
from app.handlers import admin, clients, user
from app.reminders import reminder_loop
from app.web import create_web_app

log = logging.getLogger("tattoo-bot")


async def setup_bot_ui(bot: Bot, cfg: Config) -> None:
    await bot.set_my_commands([BotCommand(command="start", description="Записаться на тату")])
    for admin_id in cfg.admin_ids:
        try:
            await bot.set_my_commands(
                [
                    BotCommand(command="start", description="Главное меню"),
                    BotCommand(command="admin", description="Панель администратора"),
                    BotCommand(command="cancel", description="Отменить действие"),
                    BotCommand(command="version", description="Какая версия бота запущена"),
                ],
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception:
            log.warning("Админ %s ещё не писал боту — команды для него не установлены", admin_id)
    if cfg.webapp_url:
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="Записаться", web_app=WebAppInfo(url=cfg.webapp_url))
            )
        except Exception as e:
            log.error("Не удалось установить кнопку меню (проверьте WEBAPP_URL): %s", e)
    else:
        log.warning("WEBAPP_URL не задан — кнопка приложения в боте не появится")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.info("Запущена версия: %s", VERSION)
    cfg = Config.from_env()
    if not cfg.admin_ids:
        log.warning("ADMIN_IDS не задан — заявки некому присылать. Узнайте свой ID командой /myid")

    db = Database(cfg.database_url, cfg.default_master)
    await db.connect()

    session = AiohttpSession(proxy=cfg.telegram_proxy) if cfg.telegram_proxy else None
    bot = Bot(cfg.bot_token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage(), db=db, cfg=cfg)
    dp.include_router(clients.router)
    dp.include_router(admin.router)
    dp.include_router(user.router)

    runner = web.AppRunner(create_web_app(bot, db, cfg))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", cfg.port).start()
    log.info("Мини-приложение доступно на порту %s", cfg.port)

    await setup_bot_ui(bot, cfg)
    reminders = asyncio.create_task(reminder_loop(bot, db, cfg))
    try:
        await dp.start_polling(bot)
    finally:
        reminders.cancel()
        await runner.cleanup()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())

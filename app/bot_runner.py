import asyncio
import logging

from aiogram import Bot
from aiogram.types import BotCommand

from .bot import create_dispatcher
from .config import settings
from .database import Database


async def main() -> None:
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is required to start the Telegram bot")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    database = Database()
    await database.initialize()
    dispatcher = create_dispatcher(
        database,
        settings.max_image_mb,
        settings.max_media_mb,
        settings.telegram_admin_username,
    )

    async with Bot(token=settings.bot_token) as bot:
        await bot.set_my_commands([
            BotCommand(command="start", description="شروع تبدیل و نمایش سهمیه"),
            BotCommand(command="help", description="راهنمای فرمت‌ها و محدودیت‌ها"),
            BotCommand(command="language", description="انتخاب زبان ربات"),
            BotCommand(command="credit", description="نمایش اعتبار و تعرفه‌ها"),
            BotCommand(command="add_credit", description="ارسال رسید شارژ اعتبار"),
            BotCommand(command="upgrade", description="ارسال رسید برای دریافت پرو"),
            BotCommand(command="cancel", description="لغو ارسال رسید"),
        ])
        await bot.set_my_commands([
            BotCommand(command="start", description="Start converting and show quota"),
            BotCommand(command="help", description="Show formats, limits, and instructions"),
            BotCommand(command="language", description="Choose Persian or English"),
            BotCommand(command="credit", description="Show balance and prices"),
            BotCommand(command="add_credit", description="Submit a credit top-up receipt"),
            BotCommand(command="upgrade", description="Submit a receipt for Pro access"),
            BotCommand(command="cancel", description="Cancel receipt submission"),
        ], language_code="en")
        # Long polling and webhooks cannot be active at the same time. Removing
        # a stale webhook makes deploys deterministic while preserving updates
        # Telegram has already queued for the bot.
        await bot.delete_webhook(drop_pending_updates=False)
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )


if __name__ == "__main__":
    asyncio.run(main())

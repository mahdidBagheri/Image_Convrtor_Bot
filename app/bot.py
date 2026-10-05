import asyncio
from dataclasses import dataclass
from time import monotonic

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from PIL import UnidentifiedImageError
from .converter import EXTENSIONS, FORMATS, convert_image
from .database import Database

PENDING_IMAGE_TTL_SECONDS = 10 * 60


@dataclass
class PendingImage:
    """An upload waiting for the user to choose an output format."""

    data: bytes
    filename: str
    created_at: float


def create_dispatcher(database: Database, max_image_mb: int) -> Dispatcher:
    """Build an isolated dispatcher and register the bot's handlers."""
    router = Router()
    pending: dict[int, PendingImage] = {}

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        await message.answer(
            "👋 Send me an image and I’ll ask which format you want.\n\n"
            "Supported: JPEG, PNG, WEBP, GIF, BMP, TIFF and PDF."
        )

    @router.message(F.photo | F.document)
    async def receive_image(message: Message, bot: Bot) -> None:
        user = message.from_user
        if user is None:
            await message.answer("I could not identify the sender of this image.")
            return
        await database.touch_user(user.id, user.username, user.first_name)
        item = message.photo[-1] if message.photo else message.document
        if item is None:
            await message.answer("Please send a valid image to begin.")
            return
        if message.document and not (message.document.mime_type or "").startswith("image/"):
            await message.answer("That file does not look like an image. Please send an image file.")
            return
        if item.file_size and item.file_size > max_image_mb * 1024 * 1024:
            await message.answer(f"That image is too large. The limit is {max_image_mb} MB.")
            return
        stream = await bot.download(item)
        if stream is None:
            await message.answer("I could not download that image. Please try again.")
            return
        data = stream.read()
        pending[user.id] = PendingImage(
            data,
            getattr(item, "file_name", None) or "image",
            monotonic(),
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=name.upper(), callback_data=f"convert:{name}"
                    )
                    for name in row
                ]
                for row in (("jpeg", "png", "webp"), ("gif", "bmp", "tiff"), ("pdf",))
            ]
        )
        await message.answer("Choose the destination format:", reply_markup=keyboard)

    @router.callback_query(F.data.startswith("convert:"))
    async def convert(callback: CallbackQuery) -> None:
        await callback.answer()
        entry = pending.get(callback.from_user.id)
        if not entry or monotonic() - entry.created_at > PENDING_IMAGE_TTL_SECONDS:
            pending.pop(callback.from_user.id, None)
            if callback.message:
                await callback.message.edit_text("This image has expired. Please send it again.")
            return
        destination = (callback.data or "").partition(":")[2]
        if destination not in FORMATS:
            if callback.message:
                await callback.message.edit_text("Unsupported format.")
            return
        if callback.message is None:
            return

        # Claim this upload before awaiting so repeated button presses cannot
        # launch duplicate conversions.
        pending.pop(callback.from_user.id, None)
        await callback.message.edit_text("⏳ Converting your image…")
        try:
            converted, source = await asyncio.to_thread(convert_image, entry.data, destination)
        except (UnidentifiedImageError, OSError, ValueError):
            await callback.message.edit_text("I could not read that image. Please try another file.")
            return
        output_format = FORMATS[destination]
        filename = f"converted.{EXTENSIONS[output_format]}"
        await callback.message.answer_document(
            BufferedInputFile(converted, filename=filename),
            caption=f"✅ {source} → {output_format}",
        )
        await database.record_conversion(
            callback.from_user.id,
            source,
            output_format,
            len(entry.data),
            len(converted),
        )
        await callback.message.delete()

    @router.message()
    async def fallback(message: Message) -> None:
        await message.answer("Please send an image to begin.")

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher

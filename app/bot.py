import asyncio
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from PIL import UnidentifiedImageError
from .converter import EXTENSIONS, FORMATS, convert_image
from .database import Database


HEIC_MIME_TYPES = {
    "application/heic",
    "application/heif",
    "image/heic",
    "image/heic-sequence",
    "image/heif",
    "image/heif-sequence",
}


def is_image_document(filename: str | None, mime_type: str | None) -> bool:
    """Accept normal image MIME types and HEIC files sent with a generic MIME type."""
    mime_type = (mime_type or "").lower()
    suffix = Path(filename or "").suffix.lower()
    return mime_type.startswith("image/") or mime_type in HEIC_MIME_TYPES or suffix in {".heic", ".heif"}


@dataclass
class PendingImage:
    data: bytes
    filename: str
    created_at: float


def create_dispatcher(database: Database, max_image_mb: int) -> Dispatcher:
    router, pending = Router(), {}

    @router.message(CommandStart())
    async def start(message: Message):
        await message.answer("👋 Send me an image and I’ll ask which format you want.\n\nHEIC input is supported. Output formats: JPEG, PNG, WEBP, GIF, BMP, TIFF and PDF.")

    @router.message(F.photo | F.document)
    async def receive_image(message: Message, bot: Bot):
        user = message.from_user
        await database.touch_user(user.id, user.username, user.first_name)
        item = message.photo[-1] if message.photo else message.document
        if message.document and not is_image_document(message.document.file_name, message.document.mime_type):
            await message.answer("That file does not look like an image. Please send an image file.")
            return
        if item.file_size and item.file_size > max_image_mb * 1024 * 1024:
            await message.answer(f"That image is too large. The limit is {max_image_mb} MB.")
            return
        stream = await bot.download(item)
        data = stream.read()
        pending[user.id] = PendingImage(data, getattr(item, "file_name", None) or "image", monotonic())
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=name.upper(), callback_data=f"convert:{name}") for name in row]
            for row in (("jpeg", "png", "webp"), ("gif", "bmp", "tiff"), ("pdf",))
        ])
        await message.answer("Choose the destination format:", reply_markup=keyboard)

    @router.callback_query(F.data.startswith("convert:"))
    async def convert(callback: CallbackQuery):
        await callback.answer()
        entry = pending.get(callback.from_user.id)
        if not entry or monotonic() - entry.created_at > 600:
            pending.pop(callback.from_user.id, None)
            await callback.message.edit_text("This image has expired. Please send it again.")
            return
        destination = callback.data.split(":", 1)[1]
        if destination not in FORMATS:
            await callback.message.edit_text("Unsupported format.")
            return
        await callback.message.edit_text("⏳ Converting your image…")
        try:
            converted, source = await asyncio.to_thread(convert_image, entry.data, destination)
        except (UnidentifiedImageError, OSError, ValueError):
            await callback.message.edit_text("I could not read that image. Please try another file.")
            pending.pop(callback.from_user.id, None)
            return
        output_format = FORMATS[destination]
        filename = f"converted.{EXTENSIONS[output_format]}"
        await callback.message.answer_document(BufferedInputFile(converted, filename=filename), caption=f"✅ {source} → {output_format}")
        await database.record_conversion(callback.from_user.id, source, output_format, len(entry.data), len(converted))
        pending.pop(callback.from_user.id, None)
        await callback.message.delete()

    @router.message()
    async def fallback(message: Message):
        await message.answer("Please send an image to begin.")

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher

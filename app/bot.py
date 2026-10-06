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


@dataclass
class PendingImage:
    data: bytes
    filename: str


@dataclass
class PendingBatch:
    images: list[PendingImage]
    created_at: float
    media_group_id: str | None = None


HEIF_EXTENSIONS = {".heic", ".heif"}


def is_image_document(message: Message) -> bool:
    """Accept normal image MIME types plus HEIC files Telegram may label generically."""
    if not message.document:
        return True
    mime_type = message.document.mime_type or ""
    filename = message.document.file_name or ""
    return mime_type.startswith("image/") or Path(filename).suffix.lower() in HEIF_EXTENSIONS


def format_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=name.upper(), callback_data=f"convert:{name}") for name in row]
        for row in (("jpeg", "png", "webp"), ("gif", "bmp", "tiff"), ("pdf",))
    ])


def create_dispatcher(database: Database, max_image_mb: int) -> Dispatcher:
    router, pending, batch_tasks = Router(), {}, {}

    async def ask_for_destination(message: Message, user_id: int):
        # Album messages arrive separately.  Waiting briefly gives Telegram time
        # to deliver every member before showing one destination picker.
        await asyncio.sleep(0.7)
        batch_tasks.pop(user_id, None)
        batch = pending.get(user_id)
        if batch:
            count = len(batch.images)
            noun = "image" if count == 1 else "images"
            await message.answer(f"Choose the destination format for {count} {noun}:", reply_markup=format_keyboard())

    @router.message(CommandStart())
    async def start(message: Message):
        await message.answer("👋 Send or forward an image (or an album) and I’ll ask which format you want.\n\nHEIC/HEIF input is supported. Output formats: JPEG, PNG, WEBP, GIF, BMP, TIFF and PDF.")

    @router.message(F.photo | F.document)
    async def receive_image(message: Message, bot: Bot):
        user = message.from_user
        await database.touch_user(user.id, user.username, user.first_name)
        item = message.photo[-1] if message.photo else message.document
        if not is_image_document(message):
            await message.answer("That file does not look like an image. Please send an image file.")
            return
        if item.file_size and item.file_size > max_image_mb * 1024 * 1024:
            await message.answer(f"That image is too large. The limit is {max_image_mb} MB.")
            return
        stream = await bot.download(item)
        data = stream.read()
        previous_task = batch_tasks.pop(user.id, None)
        if previous_task:
            previous_task.cancel()
        image = PendingImage(data, getattr(item, "file_name", None) or "image")
        existing = pending.get(user.id)
        # A media group is Telegram's representation of a multi-image upload or
        # forward.  Keep all of its members in one conversion batch.
        if message.media_group_id and existing and existing.media_group_id == message.media_group_id:
            existing.images.append(image)
        else:
            pending[user.id] = PendingBatch([image], monotonic(), message.media_group_id)

        if message.media_group_id:
            batch_tasks[user.id] = asyncio.create_task(ask_for_destination(message, user.id))
        else:
            await message.answer("Choose the destination format:", reply_markup=format_keyboard())

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
        pending.pop(callback.from_user.id, None)
        task = batch_tasks.pop(callback.from_user.id, None)
        if task:
            task.cancel()
        total = len(entry.images)
        await callback.message.edit_text(f"⏳ Converting {total} image{'s' if total != 1 else ''}…")
        output_format = FORMATS[destination]
        converted_count = 0
        for index, image in enumerate(entry.images, start=1):
            try:
                converted, source = await asyncio.to_thread(convert_image, image.data, destination)
            except (UnidentifiedImageError, OSError, ValueError):
                continue
            filename = f"converted-{index}.{EXTENSIONS[output_format]}" if total > 1 else f"converted.{EXTENSIONS[output_format]}"
            await callback.message.answer_document(BufferedInputFile(converted, filename=filename), caption=f"✅ {source} → {output_format}")
            await database.record_conversion(callback.from_user.id, source, output_format, len(image.data), len(converted))
            converted_count += 1
        if not converted_count:
            await callback.message.edit_text("I could not read any of those images. Please try another file.")
            return
        if converted_count < total:
            await callback.message.edit_text(f"Converted {converted_count} of {total} images; the remaining files could not be read.")
            return
        await callback.message.delete()

    @router.message()
    async def fallback(message: Message):
        await message.answer("Please send an image to begin.")

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher

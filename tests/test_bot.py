from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import datetime, timezone

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, MessageEntity, PhotoSize, Update, User

from app.bot import (
    CARD_NUMBER,
    add_credit_message,
    charge_summary,
    create_dispatcher,
    credit_summary,
    format_keyboard,
    help_message,
    is_image_document,
    is_media_document,
    language_keyboard,
    media_duration_allowed,
    media_keyboard,
    quota_summary,
    source_display_name,
    upgrade_message,
)


def message_with_document(filename: str, mime_type: str | None):
    return SimpleNamespace(document=SimpleNamespace(file_name=filename, mime_type=mime_type))


def test_format_keyboard_offers_every_conversion_format():
    callbacks = {
        button.callback_data
        for row in format_keyboard().inline_keyboard
        for button in row
    }
    assert callbacks == {
        "convert:jpeg",
        "convert:png",
        "convert:webp",
        "convert:gif",
        "convert:bmp",
        "convert:tiff",
        "convert:pdf",
    }


def test_language_keyboard_offers_persian_and_english():
    buttons = {
        button.callback_data: button.text
        for row in language_keyboard().inline_keyboard
        for button in row
    }
    assert buttons == {"language:fa": "🇮🇷 فارسی", "language:en": "🇬🇧 English"}


def test_accepts_images_and_heic_documents():
    assert is_image_document(message_with_document("photo.png", "image/png"))
    assert is_image_document(message_with_document("iphone.HEIC", "application/octet-stream"))
    assert is_image_document(message_with_document("forwarded.JPG", "application/octet-stream"))


def test_rejects_non_image_documents():
    assert not is_image_document(message_with_document("notes.txt", "text/plain"))


def test_accepts_audio_and_video_documents():
    assert is_media_document(message_with_document("song.mp3", "audio/mpeg"))
    assert is_media_document(message_with_document("movie.MP4", "application/octet-stream"))
    assert not is_media_document(message_with_document("notes.txt", "text/plain"))


def test_media_keyboard_offers_video_specific_choices():
    audio_callbacks = {
        button.callback_data
        for row in media_keyboard(False).inline_keyboard
        for button in row
    }
    video_callbacks = {
        button.callback_data
        for row in media_keyboard(True).inline_keyboard
        for button in row
    }
    assert audio_callbacks == {"media:mp3", "media:mp4", "media:voice"}
    assert video_callbacks == {
        "media:mp3", "media:mp4", "media:voice", "media:silent_mp4"
    }


def test_media_duration_limits_are_strict():
    assert media_duration_allowed(59.99, is_pro=False)
    assert not media_duration_allowed(60, is_pro=False)
    assert media_duration_allowed(299.99, is_pro=True)
    assert not media_duration_allowed(300, is_pro=True)


def test_quota_summary_shows_remaining_allowances_and_pro():
    assert quota_summary({"image": 4, "video": 1, "voice": 2}, "en") == (
        "Remaining today: 🖼 Images 4/5 · 🎬 Videos 1/2 · 🎙 Audio/voice 2/3"
    )
    assert "سهمیه باقی‌مانده امروز" in quota_summary(
        {"image": 4, "video": 1, "voice": 2}
    )


def test_help_describes_current_features_and_upgrade_flow():
    help_text = help_message("en")
    assert "HEIC/HEIF" in help_text
    assert "silent MP4" in help_text
    assert "Telegram voice note" in help_text
    assert "5 images, 2 videos, and 3 audio/voice" in help_text
    assert "under 1 minute" in help_text
    assert "under 5 minutes" in help_text
    assert "/upgrade" in help_text
    assert "@MahdidBagheri" in upgrade_message("MahdidBagheri", "en")
    assert quota_summary({"image": None, "video": None, "voice": None}, "en") == (
        "⭐ Pro quota: Unlimited"
    )
    assert "راهنمای ربات" in help_message()
    assert "درخواست ارتقا" in upgrade_message("MahdidBagheri")
    assert CARD_NUMBER in upgrade_message("MahdidBagheri")
    assert CARD_NUMBER in add_credit_message("MahdidBagheri")
    assert "100,000 T" in upgrade_message("MahdidBagheri", "en")


def test_credit_messages_show_toman_prices_and_charges():
    english = credit_summary(25_000, "en")
    persian = credit_summary(25_000, "fa")
    assert "25,000 T" in english and "image 2,000 T" in english
    assert "25,000 تومان" in persian and "ویدیو ۱۰٬۰۰۰" in persian
    assert "Charged 10,000 T" in charge_summary("credit", 10_000, 15_000, "en")
    assert charge_summary("free", 0, 15_000, "en") == ""


def test_heic_uses_uploaded_extension_as_display_format():
    assert source_display_name("IMG_1234.HEIC", "HEIF") == "HEIC"
    assert source_display_name("IMG_1234.heif", "HEIF") == "HEIF"
    assert source_display_name("photo.png", "PNG") == "PNG"


@pytest.mark.asyncio
async def test_start_records_telegram_user(monkeypatch):
    database = SimpleNamespace(
        touch_user=AsyncMock(),
        get_language=AsyncMock(return_value="fa"),
        get_quota_status=AsyncMock(return_value={"image": 5, "video": 2, "voice": 3}),
    )
    dispatcher = create_dispatcher(database, max_image_mb=20)
    bot = Bot("123456:abcdefghijklmnopqrstuvwxyzABCDEFGH")

    async def fake_api_call(self, method, request_timeout=None):
        return None

    monkeypatch.setattr(Bot, "__call__", fake_api_call)
    message = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=42, type="private"),
        from_user=User(id=42, is_bot=False, first_name="Test", username="tester"),
        text="/start",
        entities=[MessageEntity(type="bot_command", offset=0, length=6)],
    )

    try:
        await dispatcher.feed_update(bot, Update(update_id=1, message=message))
    finally:
        await bot.session.close()

    database.touch_user.assert_awaited_once_with(42, "tester", "Test")


@pytest.mark.asyncio
async def test_upgrade_copies_receipt_and_sends_user_link_to_admin(monkeypatch):
    database = SimpleNamespace(
        touch_user=AsyncMock(),
        get_language=AsyncMock(return_value="en"),
        is_pro=AsyncMock(return_value=False),
        get_telegram_id_by_username=AsyncMock(return_value=99),
    )
    dispatcher = create_dispatcher(database, max_image_mb=20, admin_username="MahdidBagheri")
    bot = Bot("123456:abcdefghijklmnopqrstuvwxyzABCDEFGH")
    api_calls = []

    async def fake_api_call(self, method, request_timeout=None):
        api_calls.append(method)
        return None

    monkeypatch.setattr(Bot, "__call__", fake_api_call)
    user = User(id=42, is_bot=False, first_name="Receipt", username="receipt_user")
    chat = Chat(id=42, type="private")
    upgrade = Message(
        message_id=2,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=user,
        text="/upgrade",
        entities=[MessageEntity(type="bot_command", offset=0, length=8)],
    )
    receipt = Message(
        message_id=3,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=user,
        photo=[PhotoSize(file_id="receipt", file_unique_id="receipt-unique", width=100, height=100)],
    )

    try:
        await dispatcher.feed_update(bot, Update(update_id=2, message=upgrade))
        await dispatcher.feed_update(bot, Update(update_id=3, message=receipt))
    finally:
        await bot.session.close()

    database.get_telegram_id_by_username.assert_awaited_once_with("MahdidBagheri")
    assert any(call.__class__.__name__ == "CopyMessage" and call.chat_id == 99 for call in api_calls)
    admin_messages = [
        call for call in api_calls
        if call.__class__.__name__ == "SendMessage" and call.chat_id == 99
    ]
    assert len(admin_messages) == 1
    assert "One-month Pro" in admin_messages[0].text
    assert 'tg://user?id=42' in admin_messages[0].text
    assert "@receipt_user" in admin_messages[0].text


@pytest.mark.asyncio
async def test_add_credit_receipt_is_labeled_for_admin(monkeypatch):
    database = SimpleNamespace(
        touch_user=AsyncMock(),
        get_language=AsyncMock(return_value="fa"),
        get_telegram_id_by_username=AsyncMock(return_value=99),
    )
    dispatcher = create_dispatcher(database, max_image_mb=20, admin_username="MahdidBagheri")
    bot = Bot("123456:abcdefghijklmnopqrstuvwxyzABCDEFGH")
    api_calls = []

    async def fake_api_call(self, method, request_timeout=None):
        api_calls.append(method)
        return None

    monkeypatch.setattr(Bot, "__call__", fake_api_call)
    user = User(id=43, is_bot=False, first_name="Credit", username="credit_user")
    chat = Chat(id=43, type="private")
    command = Message(
        message_id=4, date=datetime.now(timezone.utc), chat=chat, from_user=user,
        text="/add_credit",
        entities=[MessageEntity(type="bot_command", offset=0, length=11)],
    )
    receipt = Message(
        message_id=5, date=datetime.now(timezone.utc), chat=chat, from_user=user,
        photo=[PhotoSize(file_id="credit-receipt", file_unique_id="credit-unique", width=100, height=100)],
    )

    try:
        await dispatcher.feed_update(bot, Update(update_id=4, message=command))
        await dispatcher.feed_update(bot, Update(update_id=5, message=receipt))
    finally:
        await bot.session.close()

    admin_messages = [
        call for call in api_calls
        if call.__class__.__name__ == "SendMessage" and call.chat_id == 99
    ]
    assert len(admin_messages) == 1
    assert "Credit top-up" in admin_messages[0].text
    assert 'tg://user?id=43' in admin_messages[0].text

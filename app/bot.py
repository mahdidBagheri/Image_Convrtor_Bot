import asyncio
from dataclasses import dataclass
from html import escape
from pathlib import Path
from time import monotonic
from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from PIL import UnidentifiedImageError
from .converter import EXTENSIONS, FORMATS, convert_image
from .database import Database
from .media_converter import (
    FREE_DURATION_SECONDS,
    PRO_DURATION_SECONDS,
    MediaConversionError,
    convert_media,
    probe_media,
)


@dataclass
class PendingImage:
    data: bytes
    filename: str


@dataclass
class PendingBatch:
    images: list[PendingImage]
    created_at: float
    media_group_id: str | None = None


@dataclass
class PendingMedia:
    data: bytes
    filename: str
    source: str
    duration: float
    has_video: bool
    category: str
    created_at: float


IMAGE_EXTENSIONS = {
    ".avif",
    ".bmp",
    ".gif",
    ".heic",
    ".heif",
    ".jfif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}
MEDIA_EXTENSIONS = {
    ".aac", ".flac", ".m4a", ".mkv", ".mov", ".mp3", ".mp4",
    ".mpeg", ".mpg", ".oga", ".ogg", ".opus", ".wav", ".webm",
}
CARD_NUMBER = "6104338962401127"
CARD_HOLDER = "مهدی باقری"
PRO_MONTHLY_PRICE = 100_000


def is_image_document(message: Message) -> bool:
    """Accept images even when Telegram labels a forwarded document generically."""
    if not message.document:
        return True
    mime_type = message.document.mime_type or ""
    filename = message.document.file_name or ""
    return mime_type.startswith("image/") or Path(filename).suffix.lower() in IMAGE_EXTENSIONS


def is_media_document(message: Message) -> bool:
    if not message.document:
        return False
    mime_type = message.document.mime_type or ""
    filename = message.document.file_name or ""
    return (
        mime_type.startswith("audio/")
        or mime_type.startswith("video/")
        or Path(filename).suffix.lower() in MEDIA_EXTENSIONS
    )


def source_display_name(filename: str, detected_source: str) -> str:
    """Prefer HEIC/HEIF's user-facing extension over the shared HEIF container name."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".heic":
        return "HEIC"
    if suffix == ".heif":
        return "HEIF"
    return detected_source


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇮🇷 فارسی", callback_data="language:fa"),
        InlineKeyboardButton(text="🇬🇧 English", callback_data="language:en"),
    ]])


def format_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=name.upper(), callback_data=f"convert:{name}") for name in row]
        for row in (("jpeg", "png", "webp"), ("gif", "bmp", "tiff"), ("pdf",))
    ])


def media_keyboard(has_video: bool, language: str = "fa") -> InlineKeyboardMarkup:
    if has_video:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="MP4 (با صدا)" if language == "fa" else "MP4 (with audio)",
                    callback_data="media:mp4",
                ),
                InlineKeyboardButton(
                    text="MP4 بی‌صدا" if language == "fa" else "Silent MP4",
                    callback_data="media:silent_mp4",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="استخراج MP3" if language == "fa" else "Extract MP3",
                    callback_data="media:mp3",
                ),
                InlineKeyboardButton(
                    text="ویس تلگرام" if language == "fa" else "Telegram voice",
                    callback_data="media:voice",
                ),
            ],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="MP3", callback_data="media:mp3"),
        InlineKeyboardButton(text="MP4", callback_data="media:mp4"),
        InlineKeyboardButton(
            text="ویس تلگرام" if language == "fa" else "Telegram voice",
            callback_data="media:voice",
        ),
    ]])


def media_duration_limit(is_pro: bool) -> int:
    return PRO_DURATION_SECONDS if is_pro else FREE_DURATION_SECONDS


def media_duration_allowed(duration: float, is_pro: bool) -> bool:
    return duration < media_duration_limit(is_pro)


def quota_summary(status: dict[str, int | None], language: str = "fa") -> str:
    if all(remaining is None for remaining in status.values()):
        return "⭐ سهمیه پرو: نامحدود" if language == "fa" else "⭐ Pro quota: Unlimited"
    if language == "fa":
        return (
            "سهمیه باقی‌مانده امروز: "
            f"🖼 تصویر {status['image']}/{Database.FREE_DAILY_LIMITS['image']} · "
            f"🎬 ویدیو {status['video']}/{Database.FREE_DAILY_LIMITS['video']} · "
            f"🎙 صدا/ویس {status['voice']}/{Database.FREE_DAILY_LIMITS['voice']}"
        )
    return (
        "Remaining today: "
        f"🖼 Images {status['image']}/{Database.FREE_DAILY_LIMITS['image']} · "
        f"🎬 Videos {status['video']}/{Database.FREE_DAILY_LIMITS['video']} · "
        f"🎙 Audio/voice {status['voice']}/{Database.FREE_DAILY_LIMITS['voice']}"
    )


def credit_summary(balance: int, language: str = "fa") -> str:
    if language == "fa":
        return (
            f"💰 اعتبار: {balance:,} تومان\n"
            "هزینه پس از پایان سهمیه رایگان: "
            "تصویر ۲٬۰۰۰ · ویدیو ۱۰٬۰۰۰ · صدا/ویس ۵٬۰۰۰ تومان"
        )
    return (
        f"💰 Credit: {balance:,} T\n"
        "After the free quota: image 2,000 T · video 10,000 T · audio/voice 5,000 T"
    )


def charge_summary(source: str, charged: int, balance: int, language: str) -> str:
    if source != "credit":
        return ""
    if language == "fa":
        return f"💳 {charged:,} تومان از اعتبار کسر شد · مانده: {balance:,} تومان"
    return f"💳 Charged {charged:,} T credit · Balance: {balance:,} T"


def help_message(language: str = "fa") -> str:
    if language == "fa":
        return (
            "🤖 راهنمای ربات Convara\n\n"
            "روش تبدیل\n"
            "۱. یک تصویر، آلبوم تصاویر، ویدیو، فایل صوتی یا ویس تلگرام را ارسال یا فوروارد کنید.\n"
            "۲. فرمت خروجی را انتخاب کنید.\n"
            "۳. فایل تبدیل‌شده را همین‌جا دریافت کنید.\n\n"
            "فرمت‌های تصویر\n"
            "JPEG، PNG، WEBP، GIF، BMP، TIFF و PDF. تصاویر HEIC/HEIF نیز به‌عنوان ورودی پشتیبانی می‌شوند.\n\n"
            "گزینه‌های ویدیو\n"
            "MP4 با صدا، MP4 بی‌صدا، استخراج MP3 یا ویس تلگرام. حداکثر کیفیت خروجی ویدیو 1080p است.\n\n"
            "گزینه‌های صدا و ویس\n"
            "فایل صوتی و ویس ضبط‌شده در تلگرام به MP3، MP4 یا ویس تلگرام تبدیل می‌شود.\n\n"
            "طرح رایگان\n"
            "روزانه ۵ تصویر، ۲ ویدیو و ۳ تبدیل صدا/ویس بر اساس ساعت UTC. فایل رسانه‌ای باید کمتر از ۱ دقیقه باشد.\n\n"
            "طرح پرو\n"
            "ماهانه ۱۰۰٬۰۰۰ تومان، بدون محدودیت تعداد تبدیل روزانه؛ فایل رسانه‌ای باید کمتر از ۵ دقیقه باشد. برای ارسال رسید از /upgrade استفاده کنید.\n\n"
            "اعتبار\n"
            "پس از پایان سهمیه رایگان، هر تصویر ۲٬۰۰۰، هر ویدیو ۱۰٬۰۰۰ و هر صدا/ویس ۵٬۰۰۰ تومان از اعتبار کم می‌کند. برای مشاهده اعتبار /credit و برای شارژ /add_credit را بزنید.\n\n"
            "محدودیت فایل و حریم خصوصی\n"
            "حداکثر حجم ورودی ۲۰ مگابایت و حداکثر خروجی ۵۰ مگابایت است. فایل‌ها موقتاً پردازش می‌شوند و در پایگاه داده ذخیره نمی‌شوند.\n\n"
            "دستورها\n"
            "/start — شروع و نمایش سهمیه\n"
            "/help — نمایش راهنما\n"
            "/language — انتخاب زبان\n"
            "/credit — نمایش اعتبار و تعرفه‌ها\n"
            "/add_credit — ارسال رسید شارژ اعتبار\n"
            "/upgrade — ارسال رسید برای دریافت پرو\n"
            "/cancel — لغو ارسال رسید"
        )
    return (
        "🤖 Convara Bot Help\n\n"
        "How to convert\n"
        "1. Send or forward one image, an image album, video, audio file, or Telegram voice note.\n"
        "2. Choose an output format.\n"
        "3. Receive the converted file here.\n\n"
        "Image formats\n"
        "JPEG, PNG, WEBP, GIF, BMP, TIFF, and PDF. HEIC/HEIF images are supported as input.\n\n"
        "Video options\n"
        "MP4 with audio, silent MP4, extracted MP3, or Telegram voice. Video output is capped at 1080p.\n\n"
        "Audio and voice options\n"
        "Audio files and voice notes can become MP3, MP4, or Telegram voice. "
        "Voice notes recorded inside Telegram are supported.\n\n"
        "Free plan\n"
        "5 images, 2 videos, and 3 audio/voice conversions per UTC day. Media must be under 1 minute.\n\n"
        "Pro plan\n"
        "100,000 T monthly with no daily conversion quota. Media must be under 5 minutes. Use /upgrade to submit a payment receipt.\n\n"
        "Credit\n"
        "After free quotas, each image costs 2,000 T, video 10,000 T, and audio/voice 5,000 T. Use /credit to view your balance and /add_credit to top up.\n\n"
        "File limits and privacy\n"
        "Inputs are limited to 20 MB. Converted files are limited to Telegram’s 50 MB upload limit. "
        "Conversion files are processed temporarily and are not stored in the database.\n\n"
        "Commands\n"
        "/start — Start the bot and show your quota\n"
        "/help — Show this guide\n"
        "/language — Choose Persian or English\n"
        "/credit — Show balance and conversion prices\n"
        "/add_credit — Submit a credit top-up receipt\n"
        "/upgrade — Submit a receipt for Pro access\n"
        "/cancel — Cancel receipt submission"
    )


def upgrade_message(admin_username: str, language: str = "fa") -> str:
    if language == "fa":
        return (
            "⭐ درخواست ارتقا به پرو\n\n"
            "هزینه پرو یک‌ماهه: ۱۰۰٬۰۰۰ تومان\n"
            f"شماره کارت: {CARD_NUMBER}\n"
            f"به نام: {CARD_HOLDER}\n\n"
            "رسید پرداخت خود را به‌صورت عکس یا فایل ارسال کنید. عکس یا فایل بعدی شما تا ۱۰ دقیقه، "
            f"همراه با لینک حساب تلگرامتان برای بررسی به @{admin_username} ارسال می‌شود.\n\n"
            "پس از تأیید رسید، مدیر دسترسی پرو را فعال می‌کند. اگر منصرف شدید از /cancel استفاده کنید."
        )
    return (
        "⭐ Request a Pro upgrade\n\n"
        "One-month Pro price: 100,000 T\n"
        f"Card number: {CARD_NUMBER}\n"
        f"Cardholder: {CARD_HOLDER}\n\n"
        "Send your payment receipt here as a photo or document. Your next photo or document "
        f"within 10 minutes will be sent to @{admin_username} for review, together with a link "
        "to your Telegram account.\n\n"
        "The admin will activate Pro after verifying the receipt. Use /cancel if you no longer "
        "want to submit it."
    )


def add_credit_message(admin_username: str, language: str = "fa") -> str:
    if language == "fa":
        return (
            "💰 افزایش اعتبار\n\n"
            "مبلغ دلخواه را به کارت زیر واریز کنید:\n"
            f"شماره کارت: {CARD_NUMBER}\n"
            f"به نام: {CARD_HOLDER}\n\n"
            "رسید را به‌صورت عکس یا فایل ارسال کنید. رسید بعدی شما تا ۱۰ دقیقه همراه با لینک حسابتان "
            f"برای بررسی و شارژ به مبلغ رسید، به @{admin_username} ارسال می‌شود. برای لغو /cancel را بزنید."
        )
    return (
        "💰 Add credit\n\n"
        "Transfer your desired amount to:\n"
        f"Card number: {CARD_NUMBER}\n"
        f"Cardholder: {CARD_HOLDER}\n\n"
        "Send the receipt as a photo or document within 10 minutes. It will be sent with your account "
        f"link to @{admin_username}, who will add the receipt amount to your credit. Use /cancel to cancel."
    )


MESSAGES = {
    "start": {
        "fa": (
            "👋 تصویر، ویدیو، فایل صوتی یا ویس را ارسال یا فوروارد کنید تا فرمت خروجی را انتخاب کنید.\n\n"
            "تصاویر: JPEG، PNG، WEBP، GIF، BMP، TIFF، PDF.\n"
            "ویدیو: MP4 با صدا، MP4 بی‌صدا، استخراج MP3 یا ویس تلگرام؛ حداکثر 1080p.\n"
            "صدا و ویس ضبط‌شده: MP3، MP4 یا ویس تلگرام.\n\n"
            "رایگان: رسانه کمتر از ۱ دقیقه؛ روزانه ۵ تصویر، ۲ ویدیو و ۳ صدا/ویس. "
            "پرو: رسانه کمتر از ۵ دقیقه و بدون محدودیت روزانه. فایل‌ها ذخیره نمی‌شوند."
        ),
        "en": (
            "👋 Send or forward an image, video, audio file, or voice note and I’ll ask which format you want.\n\n"
            "Images: JPEG, PNG, WEBP, GIF, BMP, TIFF, PDF.\n"
            "Video: MP4 with audio, silent MP4, extracted MP3, or Telegram voice; up to 1080p.\n"
            "Audio and recorded voice: MP3, MP4, or Telegram voice.\n\n"
            "Free: media under 1 minute; 5 images, 2 videos, and 3 audio/voice conversions daily. "
            "Pro: media under 5 minutes with no daily limit. Files are not stored."
        ),
    },
    "choose_images": {
        "fa": "فرمت خروجی را برای {count} تصویر انتخاب کنید:",
        "en": "Choose the destination format for {count} {noun}:",
    },
    "choose_image": {"fa": "فرمت خروجی را انتخاب کنید:", "en": "Choose the destination format:"},
    "choose_media": {
        "fa": "فرمت خروجی را انتخاب کنید ({duration:.1f} ثانیه · {plan}):",
        "en": "Choose the destination format ({duration:.1f}s · {plan}):",
    },
    "language_prompt": {"fa": "زبان ربات را انتخاب کنید:", "en": "Choose the bot language:"},
    "language_saved": {"fa": "✅ زبان ربات روی فارسی تنظیم شد.", "en": "✅ Bot language changed to English."},
    "unknown_account": {"fa": "حساب تلگرام شما شناسایی نشد.", "en": "I could not identify your Telegram account."},
    "already_pro": {"fa": "⭐ حساب شما پرو است و سهمیه روزانه نامحدود دارد.", "en": "⭐ Your account already has Pro access with unlimited daily conversions."},
    "cancelled": {"fa": "ارسال رسید لغو شد. فایل‌های بعدی برای تبدیل پردازش می‌شوند.", "en": "Receipt submission cancelled. Your files will be treated as conversion requests again."},
    "nothing_to_cancel": {"fa": "در حال حاضر رسیدی در انتظار ارسال نیست.", "en": "There is no pending receipt submission to cancel."},
    "receipt_failed": {"fa": "ارسال خودکار رسید انجام نشد. لطفاً مستقیماً با @{admin} تماس بگیرید.", "en": "I couldn’t deliver the receipt automatically. Please contact @{admin} directly."},
    "receipt_sent": {"fa": "✅ رسید شما برای @{admin} ارسال شد. پس از بررسی به شما اطلاع داده می‌شود.", "en": "✅ Your receipt was sent to @{admin}. You’ll be notified after it is reviewed."},
    "receipt_file": {"fa": "لطفاً رسید را به‌صورت عکس یا فایل ارسال کنید، یا با /cancel از حالت ارتقا خارج شوید.", "en": "Please send the receipt as a photo or document, or use /cancel to leave upgrade mode."},
    "unknown_sender": {"fa": "فرستنده شناسایی نشد. لطفاً فایل را در گفت‌وگوی خصوصی ارسال کنید.", "en": "I could not identify the sender. Please send the file in a private chat."},
    "unsupported_file": {"fa": "این فایل، تصویر، صدا یا ویدیوی پشتیبانی‌شده‌ای نیست.", "en": "That file is not a supported image, audio, or video file."},
    "too_large": {"fa": "حجم فایل زیاد است. حداکثر حجم {limit} مگابایت است.", "en": "That file is too large. The limit is {limit} MB."},
    "unread_media": {"fa": "این فایل رسانه‌ای قابل خواندن نیست. فایل دیگری امتحان کنید.", "en": "I could not read that media file. Please try a different file."},
    "too_long": {"fa": "مدت این فایل {duration:.1f} ثانیه است. طرح {plan} فقط فایل کمتر از {limit} را می‌پذیرد.", "en": "That media is {duration:.1f} seconds long. {plan} supports media under {limit}."},
    "media_expired": {"fa": "زمان این فایل تمام شده است. دوباره آن را ارسال کنید.", "en": "This media has expired. Please send it again."},
    "unsupported_media": {"fa": "فرمت رسانه‌ای پشتیبانی نمی‌شود.", "en": "Unsupported media format."},
    "silent_video_only": {"fa": "MP4 بی‌صدا فقط برای فایل‌های ویدیویی در دسترس است.", "en": "Silent MP4 is only available for video files."},
    "quota_media": {"fa": "سهمیه رایگان امروز شما برای {kind} تمام شده و اعتبار کافی ندارید. هزینه این تبدیل {price:,} تومان و موجودی شما {balance:,} تومان است. برای شارژ /add_credit را بزنید.", "en": "Your free {kind} quota is exhausted and your credit is insufficient. This conversion costs {price:,} T; your balance is {balance:,} T. Use /add_credit to top up."},
    "converting": {"fa": "⏳ در حال تبدیل {source} ← {destination}…", "en": "⏳ Converting {source} → {destination}…"},
    "media_failed": {"fa": "تبدیل این فایل انجام نشد: {error}", "en": "I could not convert that media: {error}"},
    "image_expired": {"fa": "زمان این تصویر تمام شده است. دوباره آن را ارسال کنید.", "en": "This image has expired. Please send it again."},
    "unsupported_format": {"fa": "این فرمت پشتیبانی نمی‌شود.", "en": "Unsupported format."},
    "converting_images": {"fa": "⏳ در حال تبدیل {total} تصویر…", "en": "⏳ Converting {total} {noun}…"},
    "quota_images": {"fa": "از {total} تصویر، {converted} تصویر تبدیل شد. سهمیه رایگان تمام شده و اعتبار برای تصویر بعدی کافی نیست. هزینه هر تصویر {price:,} تومان و موجودی شما {balance:,} تومان است. برای شارژ /add_credit را بزنید.", "en": "Converted {converted} of {total} images. Your free quota is exhausted and credit is insufficient for the next image. Each image costs {price:,} T; your balance is {balance:,} T. Use /add_credit to top up."},
    "unread_images": {"fa": "هیچ‌کدام از تصاویر قابل خواندن نبودند. فایل دیگری امتحان کنید.", "en": "I could not read any of those images. Please try another file."},
    "partial_images": {"fa": "از {total} تصویر، {converted} تصویر تبدیل شد؛ بقیه فایل‌ها قابل خواندن نبودند.", "en": "Converted {converted} of {total} images; the remaining files could not be read."},
    "fallback": {"fa": "برای شروع یک تصویر، ویدیو، فایل صوتی یا ویس ارسال کنید. برای راهنما /help را بزنید.", "en": "Please send an image, video, audio file, or voice note to begin. Use /help for instructions."},
}


def tr(language: str, key: str, **values) -> str:
    language = language if language in {"fa", "en"} else "fa"
    return MESSAGES[key][language].format(**values)


def create_dispatcher(
    database: Database,
    max_image_mb: int,
    max_media_mb: int = 20,
    admin_username: str = "MahdidBagheri",
) -> Dispatcher:
    router, pending, pending_media, batch_tasks, upgrade_requests = Router(), {}, {}, {}, {}
    media_semaphore = asyncio.Semaphore(2)
    admin_username = admin_username.lstrip("@")

    async def user_language(user_id: int) -> str:
        return await database.get_language(user_id)

    async def ask_for_destination(message: Message, user_id: int):
        # Album messages arrive separately.  Waiting briefly gives Telegram time
        # to deliver every member before showing one destination picker.
        await asyncio.sleep(0.7)
        batch_tasks.pop(user_id, None)
        batch = pending.get(user_id)
        if batch:
            language = await user_language(user_id)
            count = len(batch.images)
            noun = "image" if count == 1 else "images"
            quota = quota_summary(await database.get_quota_status(user_id), language)
            await message.answer(
                f"{tr(language, 'choose_images', count=count, noun=noun)}\n\n{quota}",
                reply_markup=format_keyboard(),
            )

    @router.message(CommandStart())
    async def start(message: Message):
        user = message.from_user
        if user is not None:
            await database.touch_user(user.id, user.username, user.first_name)
            language = await user_language(user.id)
            quota = quota_summary(await database.get_quota_status(user.id), language)
        else:
            language = "fa"
            quota = ""
        await message.answer(
            f"{tr(language, 'start')}\n\n{quota}\n\n{tr(language, 'language_prompt')}",
            reply_markup=language_keyboard(),
        )

    @router.message(Command("language"))
    async def language_command(message: Message):
        user = message.from_user
        language = await user_language(user.id) if user is not None else "fa"
        await message.answer(tr(language, "language_prompt"), reply_markup=language_keyboard())

    @router.callback_query(F.data.startswith("language:"))
    async def language_callback(callback: CallbackQuery):
        await callback.answer()
        language = callback.data.split(":", 1)[1]
        if language not in {"fa", "en"}:
            return
        user = callback.from_user
        await database.touch_user(user.id, user.username, user.first_name)
        await database.set_language(user.id, language)
        quota = quota_summary(await database.get_quota_status(user.id), language)
        await callback.message.edit_text(
            f"{tr(language, 'language_saved')}\n\n{quota}",
            reply_markup=language_keyboard(),
        )

    @router.message(Command("help"))
    async def help_command(message: Message):
        user = message.from_user
        if user is not None:
            await database.touch_user(user.id, user.username, user.first_name)
            language = await user_language(user.id)
            quota = quota_summary(await database.get_quota_status(user.id), language)
            await message.answer(f"{help_message(language)}\n\n{quota}")
        else:
            await message.answer(help_message("fa"))

    @router.message(Command("credit"))
    async def credit_command(message: Message):
        user = message.from_user
        if user is None:
            await message.answer(tr("fa", "unknown_account"))
            return
        await database.touch_user(user.id, user.username, user.first_name)
        language = await user_language(user.id)
        balance = await database.get_credit_balance(user.id)
        pro_expiry = await database.get_pro_expiry(user.id)
        if language == "fa":
            plan = (
                f"⭐ طرح: پرو تا {pro_expiry:%Y-%m-%d %H:%M} UTC"
                if pro_expiry else "طرح: رایگان"
            )
        else:
            plan = (
                f"⭐ Plan: Pro until {pro_expiry:%Y-%m-%d %H:%M} UTC"
                if pro_expiry else "Plan: Free"
            )
        await message.answer(f"{credit_summary(balance, language)}\n{plan}")

    @router.message(Command("add_credit"))
    async def add_credit_command(message: Message):
        user = message.from_user
        if user is None:
            await message.answer(tr("fa", "unknown_account"))
            return
        await database.touch_user(user.id, user.username, user.first_name)
        language = await user_language(user.id)
        upgrade_requests[user.id] = ("credit", monotonic())
        await message.answer(add_credit_message(admin_username, language))

    @router.message(Command("upgrade"))
    async def upgrade_command(message: Message):
        user = message.from_user
        if user is None:
            await message.answer(tr("fa", "unknown_account"))
            return
        await database.touch_user(user.id, user.username, user.first_name)
        language = await user_language(user.id)
        upgrade_requests[user.id] = ("pro", monotonic())
        await message.answer(upgrade_message(admin_username, language))

    @router.message(Command("cancel"))
    async def cancel_command(message: Message):
        user = message.from_user
        language = await user_language(user.id) if user is not None else "fa"
        if user is not None and upgrade_requests.pop(user.id, None) is not None:
            await message.answer(tr(language, "cancelled"))
        else:
            await message.answer(tr(language, "nothing_to_cancel"))

    def waiting_for_receipt(message: Message) -> bool:
        user = message.from_user
        if user is None or user.id not in upgrade_requests:
            return False
        if monotonic() - upgrade_requests[user.id][1] > 600:
            upgrade_requests.pop(user.id, None)
            return False
        return True

    @router.message(waiting_for_receipt, F.photo | F.document)
    async def receive_upgrade_receipt(message: Message, bot: Bot):
        user = message.from_user
        if user is None:
            return
        await database.touch_user(user.id, user.username, user.first_name)
        language = await user_language(user.id)
        request_kind = upgrade_requests[user.id][0]
        admin_chat_id = await database.get_telegram_id_by_username(admin_username)
        if admin_chat_id is None:
            await message.answer(tr(language, "receipt_failed", admin=admin_username))
            return

        full_name = escape(user.full_name or user.first_name or "Telegram user")
        user_link = f'<a href="tg://user?id={user.id}">{full_name}</a>'
        username_line = (
            f'\nUsername: <a href="https://t.me/{escape(user.username)}">@{escape(user.username)}</a>'
            if user.username else ""
        )
        try:
            await bot.copy_message(admin_chat_id, message.chat.id, message.message_id)
            request_label = (
                "Credit top-up — add the amount shown on the receipt"
                if request_kind == "credit"
                else "One-month Pro — 100,000 T"
            )
            await bot.send_message(
                admin_chat_id,
                f"🧾 New payment receipt\nRequest: <b>{request_label}</b>\n"
                f"User: {user_link}{username_line}\nTelegram ID: <code>{user.id}</code>",
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except TelegramAPIError:
            await message.answer(tr(language, "receipt_failed", admin=admin_username))
            return

        upgrade_requests.pop(user.id, None)
        await message.answer(tr(language, "receipt_sent", admin=admin_username))

    @router.message(waiting_for_receipt)
    async def receipt_requires_file(message: Message):
        user = message.from_user
        language = await user_language(user.id) if user is not None else "fa"
        await message.answer(tr(language, "receipt_file"))

    @router.message(F.photo | F.document | F.video | F.audio | F.voice | F.video_note)
    async def receive_file(message: Message, bot: Bot):
        user = message.from_user
        if user is None:
            await message.answer(tr("fa", "unknown_sender"))
            return
        await database.touch_user(user.id, user.username, user.first_name)
        language = await user_language(user.id)
        is_image = bool(message.photo) or bool(message.document and is_image_document(message))
        is_media = bool(message.video or message.audio or message.voice or message.video_note) or is_media_document(message)
        if not is_image and not is_media:
            await message.answer(tr(language, "unsupported_file"))
            return

        item = (
            message.photo[-1] if message.photo else message.video or message.audio
            or message.voice or message.video_note or message.document
        )
        size_limit = max_image_mb if is_image else max_media_mb
        if item.file_size and item.file_size > size_limit * 1024 * 1024:
            await message.answer(tr(language, "too_large", limit=size_limit))
            return
        stream = await bot.download(item)
        data = stream.read()

        if is_media:
            previous_task = batch_tasks.pop(user.id, None)
            if previous_task:
                previous_task.cancel()
            pending.pop(user.id, None)
            filename = getattr(item, "file_name", None) or (
                "voice.ogg" if message.voice else "video-note.mp4" if message.video_note
                else "video.mp4" if message.video else "audio.mp3"
            )
            try:
                duration, has_video, source = await asyncio.to_thread(
                    probe_media, data, filename, bool(message.voice)
                )
            except MediaConversionError:
                await message.answer(tr(language, "unread_media"))
                return
            is_pro = await database.is_pro(user.id)
            plan = ("پرو" if is_pro else "رایگان") if language == "fa" else ("Pro" if is_pro else "Free")
            if not media_duration_allowed(duration, is_pro):
                limit_text = ("۵ دقیقه" if is_pro else "۱ دقیقه") if language == "fa" else ("5 minutes" if is_pro else "1 minute")
                await message.answer(
                    tr(language, "too_long", duration=duration, plan=plan, limit=limit_text)
                )
                return
            pending_media[user.id] = PendingMedia(
                data=data,
                filename=filename,
                source=source,
                duration=duration,
                has_video=has_video,
                category="video" if has_video else "voice",
                created_at=monotonic(),
            )
            await message.answer(
                f"{tr(language, 'choose_media', duration=duration, plan=plan)}\n\n"
                f"{quota_summary(await database.get_quota_status(user.id), language)}",
                reply_markup=media_keyboard(has_video, language),
            )
            return

        pending_media.pop(user.id, None)
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
            await message.answer(
                f"{tr(language, 'choose_image')}\n\n"
                f"{quota_summary(await database.get_quota_status(user.id), language)}",
                reply_markup=format_keyboard(),
            )

    @router.callback_query(F.data.startswith("media:"))
    async def convert_media_callback(callback: CallbackQuery):
        await callback.answer()
        language = await user_language(callback.from_user.id)
        entry = pending_media.get(callback.from_user.id)
        if not entry or monotonic() - entry.created_at > 600:
            pending_media.pop(callback.from_user.id, None)
            await callback.message.edit_text(tr(language, "media_expired"))
            return
        destination = callback.data.split(":", 1)[1]
        if destination not in {"mp3", "mp4", "voice", "silent_mp4"}:
            await callback.message.edit_text(tr(language, "unsupported_media"))
            return
        if destination == "silent_mp4" and not entry.has_video:
            await callback.message.edit_text(tr(language, "silent_video_only"))
            return
        pending_media.pop(callback.from_user.id, None)
        reservation = await database.reserve_conversion(callback.from_user.id, entry.category)
        if reservation is None:
            noun = ("ویدیو" if entry.category == "video" else "صدا/ویس") if language == "fa" else ("video" if entry.category == "video" else "audio/voice")
            balance = await database.get_credit_balance(callback.from_user.id)
            await callback.message.edit_text(
                f"{tr(language, 'quota_media', kind=noun, price=database.CREDIT_PRICES[entry.category], balance=balance)}\n\n"
                f"{quota_summary(await database.get_quota_status(callback.from_user.id), language)}"
            )
            return
        audit_label = {
            "mp3": "MP3",
            "mp4": "MP4",
            "voice": "TELEGRAM VOICE",
            "silent_mp4": "SILENT MP4",
        }[destination]
        label = {
            "mp3": "MP3",
            "mp4": "MP4",
            "voice": "ویس تلگرام" if language == "fa" else "TELEGRAM VOICE",
            "silent_mp4": "MP4 بی‌صدا" if language == "fa" else "SILENT MP4",
        }[destination]
        source_label = "ویس تلگرام" if language == "fa" and entry.source == "TELEGRAM VOICE" else entry.source
        await callback.message.edit_text(
            tr(language, "converting", source=source_label, destination=label)
        )
        try:
            async with media_semaphore:
                converted = await asyncio.to_thread(
                    convert_media, entry.data, destination, entry.has_video
                )
        except MediaConversionError as error:
            await database.release_reservation(reservation.id)
            await callback.message.edit_text(
                f"{tr(language, 'media_failed', error=error)}\n\n"
                f"{quota_summary(await database.get_quota_status(callback.from_user.id), language)}"
            )
            return

        caption = (
            f"✅ {source_label} → {label}\n\n"
            f"{quota_summary(await database.get_quota_status(callback.from_user.id), language)}"
        )
        if charge_note := charge_summary(
            reservation.source, reservation.charged, reservation.balance, language
        ):
            caption += f"\n{charge_note}"
        try:
            if destination == "mp3":
                await callback.message.answer_audio(
                    BufferedInputFile(converted, filename="converted.mp3"), caption=caption
                )
            elif destination in {"mp4", "silent_mp4"}:
                filename = "converted-silent.mp4" if destination == "silent_mp4" else "converted.mp4"
                await callback.message.answer_video(
                    BufferedInputFile(converted, filename=filename), caption=caption,
                    supports_streaming=True,
                )
            else:
                await callback.message.answer_voice(
                    BufferedInputFile(converted, filename="converted.ogg"), caption=caption
                )
        except Exception:
            await database.release_reservation(reservation.id)
            raise
        await database.record_conversion(
            callback.from_user.id, entry.source, audit_label, len(entry.data), len(converted),
            reservation.id,
        )
        await callback.message.delete()

    @router.callback_query(F.data.startswith("convert:"))
    async def convert(callback: CallbackQuery):
        await callback.answer()
        language = await user_language(callback.from_user.id)
        entry = pending.get(callback.from_user.id)
        if not entry or monotonic() - entry.created_at > 600:
            pending.pop(callback.from_user.id, None)
            await callback.message.edit_text(tr(language, "image_expired"))
            return
        destination = callback.data.split(":", 1)[1]
        if destination not in FORMATS:
            await callback.message.edit_text(tr(language, "unsupported_format"))
            return
        pending.pop(callback.from_user.id, None)
        task = batch_tasks.pop(callback.from_user.id, None)
        if task:
            task.cancel()
        total = len(entry.images)
        noun = "image" if total == 1 else "images"
        await callback.message.edit_text(
            tr(language, "converting_images", total=total, noun=noun)
        )
        output_format = FORMATS[destination]
        converted_count = 0
        quota_exhausted = False
        for index, image in enumerate(entry.images, start=1):
            reservation = await database.reserve_conversion(callback.from_user.id, "image")
            if reservation is None:
                quota_exhausted = True
                break
            try:
                converted, detected_source = await asyncio.to_thread(convert_image, image.data, destination)
            except (UnidentifiedImageError, OSError, ValueError, KeyError):
                await database.release_reservation(reservation.id)
                continue
            source = source_display_name(image.filename, detected_source)
            filename = f"converted-{index}.{EXTENSIONS[output_format]}" if total > 1 else f"converted.{EXTENSIONS[output_format]}"
            try:
                quota = quota_summary(
                    await database.get_quota_status(callback.from_user.id), language
                )
                await callback.message.answer_document(
                    BufferedInputFile(converted, filename=filename),
                    caption=(
                        f"✅ {source} → {output_format}\n\n{quota}"
                        + (
                            f"\n{charge_summary(reservation.source, reservation.charged, reservation.balance, language)}"
                            if reservation.source == "credit" else ""
                        )
                    ),
                )
            except Exception:
                await database.release_reservation(reservation.id)
                raise
            await database.record_conversion(
                callback.from_user.id, source, output_format, len(image.data), len(converted),
                reservation.id,
            )
            converted_count += 1
        if quota_exhausted:
            balance = await database.get_credit_balance(callback.from_user.id)
            await callback.message.edit_text(
                f"{tr(language, 'quota_images', converted=converted_count, total=total, price=database.CREDIT_PRICES['image'], balance=balance)}\n\n"
                f"{quota_summary(await database.get_quota_status(callback.from_user.id), language)}"
            )
            return
        if not converted_count:
            await callback.message.edit_text(tr(language, "unread_images"))
            return
        if converted_count < total:
            await callback.message.edit_text(
                tr(language, "partial_images", converted=converted_count, total=total)
            )
            return
        await callback.message.delete()

    @router.message()
    async def fallback(message: Message):
        user = message.from_user
        language = await user_language(user.id) if user is not None else "fa"
        await message.answer(tr(language, "fallback"))

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher

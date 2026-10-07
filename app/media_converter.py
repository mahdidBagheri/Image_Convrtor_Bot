import json
import math
from pathlib import Path
import subprocess
import tempfile


FREE_DURATION_SECONDS = 60
PRO_DURATION_SECONDS = 300
MAX_OUTPUT_BYTES = 50 * 1024 * 1024


class MediaConversionError(ValueError):
    pass


def _run(command: list[str], data: bytes, timeout: int) -> bytes:
    try:
        # A temporary seekable input is required for MP4 files whose metadata is
        # stored at the end. It is removed as soon as this subprocess exits.
        with tempfile.NamedTemporaryFile(prefix="pixelshift-media-") as input_file:
            input_file.write(data)
            input_file.flush()
            resolved_command = [
                input_file.name if argument == "pipe:0" else argument
                for argument in command
            ]
            process = subprocess.run(
                resolved_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=timeout,
            )
    except subprocess.TimeoutExpired as error:
        raise MediaConversionError("Media processing timed out") from error
    if process.returncode:
        detail = process.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise MediaConversionError(detail[-1] if detail else "Media processing failed")
    return process.stdout


def probe_media(data: bytes, filename: str, telegram_voice: bool = False) -> tuple[float, bool, str]:
    output = _run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,format_name:stream=codec_type", "-of", "json", "pipe:0",
    ], data, timeout=30)
    try:
        details = json.loads(output)
        duration = float(details["format"]["duration"])
        has_video = any(stream.get("codec_type") == "video" for stream in details.get("streams", []))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise MediaConversionError("Could not determine the media duration") from error
    if not math.isfinite(duration) or duration <= 0:
        raise MediaConversionError("Could not determine the media duration")

    if telegram_voice:
        source = "TELEGRAM VOICE"
    else:
        suffix = Path(filename).suffix.lower().lstrip(".")
        source = suffix.upper() if suffix else details["format"].get("format_name", "MEDIA").split(",")[0].upper()
    return duration, has_video, source


def convert_media(data: bytes, destination: str, has_video: bool) -> bytes:
    base = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0"]
    if destination == "mp3":
        command = base + [
            "-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-b:a", "192k",
            "-f", "mp3", "pipe:1",
        ]
    elif destination == "voice":
        command = base + [
            "-map", "0:a:0", "-vn", "-c:a", "libopus", "-b:a", "48k",
            "-ac", "1", "-application", "voip", "-f", "ogg", "pipe:1",
        ]
    elif destination == "mp4" and has_video:
        command = base + [
            "-map", "0:v:0", "-map", "0:a:0?",
            "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+frag_keyframe+empty_moov", "-f", "mp4", "pipe:1",
        ]
    elif destination == "silent_mp4" and has_video:
        command = base + [
            "-map", "0:v:0", "-an",
            "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-movflags", "+frag_keyframe+empty_moov", "-f", "mp4", "pipe:1",
        ]
    elif destination == "mp4":
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=0x17212b:s=1280x720:r=24",
            "-i", "pipe:0", "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
            "-crf", "28", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-shortest", "-movflags", "+frag_keyframe+empty_moov", "-f", "mp4", "pipe:1",
        ]
    else:
        raise MediaConversionError("Unsupported media destination")

    output = _run(command, data, timeout=480)
    if not output:
        raise MediaConversionError("The converted media is empty")
    if len(output) > MAX_OUTPUT_BYTES:
        raise MediaConversionError("The converted file exceeds Telegram's 50 MB upload limit")
    return output

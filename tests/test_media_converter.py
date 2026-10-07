from io import BytesIO
import math
import subprocess
import struct
import wave

import pytest

from app.media_converter import MediaConversionError, convert_media, probe_media


def sample_wav(seconds: float = 0.25) -> bytes:
    rate = 8_000
    frames = int(rate * seconds)
    output = BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"".join(
            struct.pack("<h", int(8_000 * math.sin(2 * math.pi * 440 * i / rate)))
            for i in range(frames)
        ))
    return output.getvalue()


def sample_video() -> bytes:
    process = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=8000",
        "-t", "0.3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-movflags", "+frag_keyframe+empty_moov",
        "-f", "mp4", "pipe:1",
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return process.stdout


def test_probe_and_convert_audio_formats():
    source = sample_wav()
    duration, has_video, source_format = probe_media(source, "sample.wav")
    assert 0.2 <= duration <= 0.3
    assert not has_video
    assert source_format == "WAV"

    mp3 = convert_media(source, "mp3", has_video=False)
    voice = convert_media(mp3, "voice", has_video=False)
    video = convert_media(voice, "mp4", has_video=False)

    assert probe_media(mp3, "converted.mp3")[2] == "MP3"
    assert probe_media(voice, "voice.ogg", telegram_voice=True)[2] == "TELEGRAM VOICE"
    assert probe_media(video, "converted.mp4")[1]


def test_video_can_extract_audio_or_remove_it():
    source = sample_video()
    duration, has_video, source_format = probe_media(source, "sample.mp4")
    assert 0.2 <= duration < 1
    assert has_video
    assert source_format == "MP4"

    extracted_audio = convert_media(source, "mp3", has_video=True)
    silent_video = convert_media(source, "silent_mp4", has_video=True)

    assert probe_media(extracted_audio, "extracted.mp3")[2] == "MP3"
    assert probe_media(silent_video, "silent.mp4")[1]
    # A silent output must not expose an audio stream for another conversion.
    with pytest.raises(MediaConversionError):
        convert_media(silent_video, "mp3", has_video=True)

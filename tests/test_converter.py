from io import BytesIO

import pytest
from PIL import Image

from app.converter import convert_image


def sample_image(mode="RGBA"):
    stream = BytesIO()
    Image.new(mode, (12, 8), (255, 0, 0, 128) if mode == "RGBA" else "red").save(stream, "PNG")
    return stream.getvalue()


def test_converts_png_to_webp():
    result, source = convert_image(sample_image("RGB"), "webp")
    assert source == "PNG"
    assert Image.open(BytesIO(result)).format == "WEBP"


def test_flattens_transparency_for_jpeg():
    result, _ = convert_image(sample_image(), "jpeg")
    converted = Image.open(BytesIO(result))
    assert converted.format == "JPEG"
    assert converted.mode == "RGB"


def test_rejects_unsupported_destination():
    with pytest.raises(ValueError, match="Unsupported destination format"):
        convert_image(sample_image(), "raw")


def test_destination_is_case_insensitive():
    result, _ = convert_image(sample_image("RGB"), "PNG")
    assert Image.open(BytesIO(result)).format == "PNG"

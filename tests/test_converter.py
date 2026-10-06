from io import BytesIO

import pytest
from PIL import Image
from pillow_heif import register_heif_opener

from app.bot import is_image_document
from app.converter import convert_image


register_heif_opener()


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


@pytest.mark.parametrize("destination", ["jpeg", "png", "webp", "gif", "bmp", "tiff", "pdf"])
def test_converts_heic_to_every_output_format(destination):
    source = BytesIO()
    Image.new("RGB", (12, 8), "blue").save(source, format="HEIF", quality=-1)

    result, detected_source = convert_image(source.getvalue(), destination)

    assert detected_source == "HEIF"
    if destination == "pdf":
        assert result.startswith(b"%PDF")
    else:
        assert Image.open(BytesIO(result)).format == destination.upper().replace("JPG", "JPEG")


@pytest.mark.parametrize("filename", ["photo.heic", "photo.HEIF"])
def test_accepts_heic_documents_with_generic_mime_type(filename):
    assert is_image_document(filename, "application/octet-stream")

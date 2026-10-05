"""Image conversion primitives used by the Telegram bot."""

from io import BytesIO

from PIL import Image, ImageOps


FORMATS = {"jpeg": "JPEG", "png": "PNG", "webp": "WEBP", "gif": "GIF", "bmp": "BMP", "tiff": "TIFF", "pdf": "PDF"}
EXTENSIONS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif", "BMP": "bmp", "TIFF": "tiff", "PDF": "pdf"}


def convert_image(data: bytes, destination: str) -> tuple[bytes, str]:
    """Convert encoded image data to a supported destination format."""
    try:
        output_format = FORMATS[destination.casefold()]
    except KeyError as exc:
        raise ValueError(f"Unsupported destination format: {destination}") from exc
    with Image.open(BytesIO(data)) as image:
        source = (image.format or "UNKNOWN").upper()
        image = ImageOps.exif_transpose(image)
        if output_format in {"JPEG", "PDF"} and image.mode not in {"RGB", "L"}:
            background = Image.new("RGB", image.size, "white")
            if "A" in image.getbands():
                background.paste(image, mask=image.getchannel("A"))
            else:
                background.paste(image.convert("RGB"))
            image = background
        output = BytesIO()
        options = {"quality": 90, "optimize": True} if output_format in {"JPEG", "WEBP"} else {}
        image.save(output, format=output_format, **options)
        return output.getvalue(), source

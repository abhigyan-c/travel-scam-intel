"""Bounded raster validation and metadata-free normalized private review images."""

import hashlib
import io
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 10_000_000
MAX_ATTACHMENTS = 4
TYPES = {"JPEG": "image/jpeg", "PNG": "image/png"}


class MediaError(ValueError):
    def __init__(self, code, status=422):
        self.code, self.status = code, status
        super().__init__(code)


@dataclass(frozen=True)
class ValidatedImage:
    original_hash: str
    stored_hash: str
    content: bytes
    media_type: str
    width: int
    height: int


def validate_image(raw: bytes, declared_type: str) -> ValidatedImage:
    if not raw or len(raw) > MAX_BYTES:
        raise MediaError("invalid_media_size", 413)
    if declared_type not in TYPES.values():
        raise MediaError("unsupported_media_type", 415)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                fmt = image.format
                if TYPES.get(fmt) != declared_type:
                    raise MediaError("media_type_mismatch", 415)
                if image.width * image.height > MAX_PIXELS or getattr(image, "n_frames", 1) != 1:
                    raise MediaError("unsupported_image_dimensions_or_animation")
                image.verify()
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
                pixels = ImageOps.exif_transpose(image).convert("RGBA" if fmt == "PNG" else "RGB")
                normalized = Image.new(pixels.mode, pixels.size)
                normalized.paste(pixels)
                output = io.BytesIO()
                normalized.save(output, format=fmt)
                content = output.getvalue()
                if len(content) > MAX_BYTES:
                    raise MediaError("normalized_media_too_large", 413)
                return ValidatedImage(
                    hashlib.sha256(raw).hexdigest(),
                    hashlib.sha256(content).hexdigest(),
                    content,
                    declared_type,
                    normalized.width,
                    normalized.height,
                )
    except MediaError:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise MediaError("invalid_image") from exc

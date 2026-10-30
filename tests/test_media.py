import hashlib
import io

import pytest
from PIL import Image, PngImagePlugin

from app.media import MAX_BYTES, MediaError, validate_image


def picture(fmt="PNG", size=(16, 12)):
    image = Image.new("RGB", size, (20, 30, 40))
    output = io.BytesIO()
    image.save(output, format=fmt)
    return output.getvalue()


@pytest.mark.parametrize("fmt,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg")])
def test_decoded_images_have_verified_hashes_and_bounds(fmt, mime):
    raw = picture(fmt)
    result = validate_image(raw, mime)
    assert result.original_hash == hashlib.sha256(raw).hexdigest()
    assert result.stored_hash == hashlib.sha256(result.content).hexdigest()
    assert (result.width, result.height) == (16, 12)
    with Image.open(io.BytesIO(result.content)) as normalized:
        assert normalized.format == fmt
        assert not normalized.getexif()


def test_png_text_and_jpeg_exif_are_removed_and_orientation_is_applied():
    image = Image.new("RGB", (16, 12))
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private", "traveler@example.invalid")
    output = io.BytesIO()
    image.save(output, format="PNG", pnginfo=metadata)
    result = validate_image(output.getvalue(), "image/png")
    assert b"traveler@example.invalid" not in result.content
    output = io.BytesIO()
    exif = Image.Exif()
    exif[274], exif[315] = 6, "Private author"
    image.save(output, format="JPEG", exif=exif)
    result = validate_image(output.getvalue(), "image/jpeg")
    assert (result.width, result.height) == (12, 16)
    with Image.open(io.BytesIO(result.content)) as decoded:
        assert not decoded.getexif()


@pytest.mark.parametrize(
    "raw,mime,status",
    [
        (b"", "image/png", 413),
        (b"x" * (MAX_BYTES + 1), "image/png", 413),
        (b"<svg></svg>", "image/svg+xml", 415),
        (b"%PDF-1.7", "application/pdf", 415),
        (b"bad", "image/png", 422),
    ],
    ids=["empty", "oversized", "svg", "pdf", "corrupt"],
)
def test_unsupported_corrupt_and_oversized_content_is_rejected(raw, mime, status):
    with pytest.raises(MediaError) as result:
        validate_image(raw, mime)
    assert result.value.status == status


def test_declared_type_must_match_actual_decoder_format():
    with pytest.raises(MediaError, match="media_type_mismatch"):
        validate_image(picture("PNG"), "image/jpeg")


def test_pixel_bombs_and_animated_images_are_not_loaded():
    with pytest.raises(MediaError, match="dimensions_or_animation"):
        validate_image(picture(size=(10001, 1000)), "image/png")
    output = io.BytesIO()
    images = [Image.new("RGB", (16, 12), color) for color in ("red", "blue")]
    images[0].save(output, format="PNG", save_all=True, append_images=images[1:])
    with pytest.raises(MediaError, match="dimensions_or_animation"):
        validate_image(output.getvalue(), "image/png")

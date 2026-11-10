from __future__ import annotations

import io
import secrets
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from .db import Database
from .errors import AppError, describe

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 50_000_000
MAX_IMAGE_SIDE = 2400
MAX_TEXT = 2000
MAX_NOTE = 200


def store_image(data: bytes, directory: Path, entry_id: int) -> str:
    """Validate, normalize to JPEG and save an uploaded screenshot. Returns the stored filename."""
    if not data:
        raise AppError("image_empty", "The uploaded image is empty", 422)
    if len(data) > MAX_IMAGE_BYTES:
        raise AppError("image_too_large", f"Image is larger than {MAX_IMAGE_BYTES // 1024 // 1024} MB", 413)
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.width * probe.height > MAX_IMAGE_PIXELS:
                raise AppError("image_too_large",
                               f"Image is {probe.width}x{probe.height}, over the {MAX_IMAGE_PIXELS // 1_000_000} megapixel limit", 413)
            probe.load()
            img = ImageOps.exif_transpose(probe).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise AppError("image_invalid", f"The upload is not a readable image. {describe(exc)}", 422) from exc
    img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{entry_id}-{secrets.token_hex(6)}.jpg"
    img.save(directory / filename, "JPEG", quality=92, optimize=True)
    return filename


def add_text_clip(db: Database, entry_id: int, kind: str, text: str, note: str = "") -> dict[str, Any]:
    text, note = text.strip(), note.strip()
    if not text:
        raise AppError("clip_empty", "text must not be empty", 422)
    if len(text) > MAX_TEXT:
        raise AppError("clip_too_long", f"text is {len(text)} characters, the limit is {MAX_TEXT}", 422)
    if len(note) > MAX_NOTE:
        raise AppError("clip_too_long", f"note is {len(note)} characters, the limit is {MAX_NOTE}", 422)
    if db.find_clip(entry_id, kind, text):
        raise AppError("clip_already_exists", f"This {kind} is already saved for entry {entry_id}", 409)
    return db.add_clip(entry_id, kind, text, note)

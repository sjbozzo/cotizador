from __future__ import annotations

import base64
import hashlib
import io
import re
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from .. import db


DATA_URI_RE = re.compile(r"^data:image/(webp|png|jpeg|gif);base64,(.*)$", flags=re.I | re.S)
MAX_SOURCE_BYTES = 5 * 1024 * 1024
MAX_DIMENSION = 1280


class InvalidImage(ValueError):
    pass


def process_photo(value: str) -> dict[str, Any]:
    """Keep remote URLs or compress embedded raster images to a deduplicated WebP."""
    value = value.strip()
    if not value:
        return {"photo_url": "", "image_id": None}
    match = DATA_URI_RE.fullmatch(value)
    if not match:
        return {"photo_url": value, "image_id": None}
    try:
        source = base64.b64decode(match.group(2), validate=True)
    except (ValueError, TypeError) as exc:
        raise InvalidImage("La foto embebida no contiene base64 válido") from exc
    if len(source) > MAX_SOURCE_BYTES:
        raise InvalidImage("La foto supera el máximo de 5 MB")
    try:
        with Image.open(io.BytesIO(source)) as opened:
            image = ImageOps.exif_transpose(opened)
            image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidImage("No fue posible leer la foto") from exc
    if image.width < 1 or image.height < 1 or image.width * image.height > 36_000_000:
        raise InvalidImage("Las dimensiones de la foto no son válidas")
    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGBA" if "transparency" in image.info else "RGB")
    image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, format="WEBP", quality=80, method=6, lossless=False)
    webp = output.getvalue()
    digest = hashlib.sha256(webp).hexdigest()
    image_id = db.store_image(webp, digest, "image/webp", image.width, image.height)
    return {"photo_url": "", "image_id": image_id}


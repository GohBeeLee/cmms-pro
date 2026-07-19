"""
Photo storage — saves work order photos to disk instead of embedding them
as base64 text in the description column.

Why this exists: base64 photos embedded in `description` were being loaded
into memory in FULL on every work order list / analysis / export request
(even ones that never display an image), which is what was driving RAM
usage up on Render's 512MB Starter plan as the number of completed work
orders with photos grew. Storing files on disk and only returning short
URL strings in API responses means:
  - List/analysis payloads stay tiny regardless of how many photos exist.
  - Actual image bytes are streamed straight from disk by StaticFiles,
    not held in the FastAPI process's memory as part of a JSON response.
  - A separate, much smaller thumbnail is generated for list/grid views,
    so browsing a list of work orders never pulls full-resolution images
    at all — only the detail/lightbox view does.

Uses the same RENDER_DISK_PATH env var as db.py, so photos live on the
same persistent disk as cmms.db and survive restarts/redeploys. Falls
back to a local folder next to this file when running outside Render.
"""
import base64
import os
import uuid
from io import BytesIO
from typing import Optional

from PIL import Image, ImageOps

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_render_disk = os.environ.get("RENDER_DISK_PATH")
PHOTOS_DIR = os.path.join(_render_disk, "photos") if _render_disk else os.path.join(BASE_DIR, "photos")
os.makedirs(PHOTOS_DIR, exist_ok=True)

THUMB_MAX_DIM = 320   # px, longest side — used in list/grid thumbnails
FULL_MAX_DIM  = 1600  # px, longest side — used in the detail/lightbox view
THUMB_QUALITY = 65
FULL_QUALITY  = 78


def _decode_data_url(data_url: str) -> bytes:
    """Accepts either a full 'data:image/...;base64,XXXX' URL or raw base64."""
    raw = data_url.split(",", 1)[1] if data_url.startswith("data:") and "," in data_url else data_url
    return base64.b64decode(raw)


def _resized_jpeg_bytes(im: Image.Image, max_dim: int, quality: int) -> bytes:
    im = ImageOps.exif_transpose(im)  # respect phone camera orientation
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    w, h = im.size
    scale = min(1.0, max_dim / max(w, h))
    if scale < 1.0:
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    buf = BytesIO()
    im.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def save_photo(data_url: str, subdir: str) -> Optional[dict]:
    """
    Decodes a base64 data URL, writes a compressed full-size JPEG and a
    small thumbnail JPEG under PHOTOS_DIR/subdir/, and returns the relative
    paths (for building /photos/... URLs) plus byte sizes. Returns None if
    the image can't be decoded (corrupt upload) — callers should skip it
    rather than fail the whole request over one bad photo.
    """
    try:
        raw = _decode_data_url(data_url)
        im = Image.open(BytesIO(raw))
        im.load()
    except Exception:
        return None

    photo_id = uuid.uuid4().hex
    rel_dir = subdir.strip("/")
    abs_dir = os.path.join(PHOTOS_DIR, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)

    full_bytes = _resized_jpeg_bytes(im, FULL_MAX_DIM, FULL_QUALITY)
    thumb_bytes = _resized_jpeg_bytes(im, THUMB_MAX_DIM, THUMB_QUALITY)

    full_name  = f"{photo_id}_full.jpg"
    thumb_name = f"{photo_id}_thumb.jpg"
    with open(os.path.join(abs_dir, full_name), "wb") as f:
        f.write(full_bytes)
    with open(os.path.join(abs_dir, thumb_name), "wb") as f:
        f.write(thumb_bytes)

    return {
        "full_path":  f"{rel_dir}/{full_name}",
        "thumb_path": f"{rel_dir}/{thumb_name}",
        "full_size":  len(full_bytes),
        "thumb_size": len(thumb_bytes),
    }


def delete_photo_files(*rel_paths: str) -> None:
    """Best-effort delete of photo files given their relative paths."""
    for rel in rel_paths:
        if not rel:
            continue
        try:
            os.remove(os.path.join(PHOTOS_DIR, rel))
        except OSError:
            pass
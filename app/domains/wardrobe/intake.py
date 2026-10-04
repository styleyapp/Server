import asyncio
import base64
import io
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from app.domains.wardrobe.schemas import Candidate, GarmentLabels
from app.integrations.ai.bria import Bria
from app.integrations.ai.gemini import Gemini

register_heif_opener()


class CatalogImageError(Exception):
    """A detected garment could not be turned into a catalog image."""


def _jpeg(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.convert("RGB").save(output, format="JPEG", quality=88, optimize=True)
    return output.getvalue()


def _frames(content: bytes, is_video: bool, ffmpeg_binary: str) -> list[bytes]:
    if not is_video:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(content)))
        image.thumbnail((1600, 1600))
        return [_jpeg(image)]
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "source.mp4"
        source.write_bytes(content)
        pattern = Path(directory) / "frame-%02d.jpg"
        subprocess.run(
            [
                ffmpeg_binary,
                "-v",
                "error",
                "-t",
                "12",
                "-i",
                str(source),
                "-vf",
                "fps=1,scale='min(1280,iw)':-2",
                "-frames:v",
                "12",
                str(pattern),
            ],
            check=True,
            timeout=35,
            capture_output=True,
        )
        return [path.read_bytes() for path in sorted(Path(directory).glob("frame-*.jpg"))]


def _box(raw: object, width: int, height: int) -> tuple[int, int, int, int] | None:
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    try:
        x1, y1, x2, y2 = (int(value) for value in raw)
    except (TypeError, ValueError):
        return None
    box = (max(0, x1), max(0, y1), min(width, x2), min(height, y2))
    if box[2] - box[0] < 48 or box[3] - box[1] < 48:
        return None
    return box


def _gemini_box(raw: object, width: int, height: int) -> tuple[int, int, int, int] | None:
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    if any(type(value) is not int or value < 0 or value > 1000 for value in raw):
        return None
    y1, x1, y2, x2 = raw
    return _box(
        [x1 * width // 1000, y1 * height // 1000, x2 * width // 1000, y2 * height // 1000],
        width,
        height,
    )


def _label(value: object, limit: int = 80) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _fingerprint(image: Image.Image) -> int:
    pixels = list(image.convert("L").resize((8, 8)).tobytes())
    average = sum(pixels) / len(pixels)
    return sum((1 << index) for index, value in enumerate(pixels) if value >= average)


def _similar(left: int, right: int) -> bool:
    return (left ^ right).bit_count() <= 6


async def analyze(
    content: bytes,
    *,
    is_video: bool,
    ffmpeg_binary: str,
    gemini: Gemini,
    background_remover: Bria | None = None,
) -> list[Candidate]:
    frames = await asyncio.to_thread(_frames, content, is_video, ffmpeg_binary)
    candidates: list[Candidate] = []
    seen: list[tuple[str, int]] = []
    for frame in frames:
        image = Image.open(io.BytesIO(frame)).convert("RGB")
        objects = await gemini.detect_garments(frame)
        for detected in objects:
            if not isinstance(detected, dict):
                continue
            bounds = _gemini_box(detected.get("box_2d"), *image.size)
            if bounds is None:
                continue
            category = _label(detected.get("category"))
            item_type = _label(detected.get("type"))
            color = _label(detected.get("color"))
            name = _label(detected.get("name"), 120)
            crop = image.crop(bounds)
            fingerprint = _fingerprint(crop)
            signature = f"{category}/{item_type}/{color}".lower()
            if any(
                label == signature and _similar(previous, fingerprint) for label, previous in seen
            ):
                continue
            if len(candidates) >= 8:
                return candidates
            seen.append((signature, fingerprint))
            crop_bytes = _jpeg(crop)
            try:
                cleaned = await gemini.create_catalog_image(crop_bytes, name or "garment")
                if background_remover is not None:
                    cleaned = await background_remover.remove_background(cleaned)
                mime = "image/png"
                clean_image = Image.open(io.BytesIO(cleaned)).convert("RGBA")
                clean_image.thumbnail((1024, 1024))
                output = io.BytesIO()
                clean_image.save(output, format="PNG", optimize=True)
                preview = output.getvalue()
                if len(preview) > 2_000_000:
                    clean_image.thumbnail((640, 640))
                    output = io.BytesIO()
                    clean_image.save(output, format="PNG", optimize=True)
                    preview = output.getvalue()
                    if len(preview) > 2_000_000:
                        raise ValueError("Catalog cutout exceeds preview limit")
            except Exception as error:
                # Review must never silently substitute the original photographed crop.
                raise CatalogImageError("Catalog image generation failed") from error
            raw_tags = detected.get("tags")
            tags = (
                [_label(tag, 40) for tag in raw_tags[:5] if _label(tag, 40)]
                if isinstance(raw_tags, list)
                else []
            )
            raw_hebrew = detected.get("hebrew")
            hebrew = None
            if isinstance(raw_hebrew, dict) and _label(raw_hebrew.get("name"), 120):
                hebrew = GarmentLabels(
                    name=_label(raw_hebrew.get("name"), 120),
                    category=_label(raw_hebrew.get("category")),
                    type=_label(raw_hebrew.get("type")),
                    color=_label(raw_hebrew.get("color")),
                    season=_label(raw_hebrew.get("season")),
                    tags=[
                        _label(tag, 40) for tag in raw_hebrew.get("tags", [])[:5] if _label(tag, 40)
                    ]
                    if isinstance(raw_hebrew.get("tags"), list)
                    else [],
                )
            length = detected.get("length")
            candidates.append(
                Candidate(
                    id=str(uuid4()),
                    name=(name or item_type or category or "Clothing")[:120],
                    category=category,
                    type=item_type,
                    color=color,
                    season=_label(detected.get("season")),
                    tags=tags,
                    hebrew=hebrew,
                    length=length if length in ("short", "regular", "long") else "",
                    image_base64=base64.b64encode(preview).decode(),
                    image_mime=mime,
                    source="video" if is_video else "photo",
                )
            )
            if len(candidates) >= 8:
                return candidates
    return candidates

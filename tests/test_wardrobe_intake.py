import asyncio
import base64
import io

import httpx
import pytest
from PIL import Image

from app.domains.wardrobe.intake import CatalogImageError, analyze


class FakeGemini:
    def __init__(self, worn: bool = False) -> None:
        self.worn = worn

    async def detect_garments(self, image: bytes) -> list[dict]:
        return [
            {
                "box_2d": [70, 70, 790, 790],
                "name": "Blue hoodie",
                "category": "Tops",
                "type": "Hoodie",
                "color": "Blue",
                "season": "Winter",
                "tags": ["Casual"],
                "length": "regular",
                "hebrew": {
                    "name": "קפוצ׳ון כחול",
                    "category": "חלק עליון",
                    "type": "קפוצ׳ון",
                    "color": "כחול",
                    "season": "חורף",
                    "tags": ["יומיומי"],
                },
                "worn": self.worn,
            },
            {"box_2d": [0, 0, 10, 10], "name": "noise"},
        ]

    async def create_catalog_image(self, image: bytes, name: str) -> bytes:
        assert name == "Blue hoodie"
        output = io.BytesIO()
        Image.new("RGB", (100, 100), "red").save(output, format="PNG")
        return output.getvalue()


def _source() -> bytes:
    source = io.BytesIO()
    Image.new("RGB", (140, 140), "blue").save(source, format="JPEG")
    return source.getvalue()


def test_flat_garment_returns_generated_catalog_image() -> None:
    candidates = asyncio.run(
        analyze(_source(), is_video=False, ffmpeg_binary="ffmpeg", gemini=FakeGemini())
    )
    assert len(candidates) == 1
    assert candidates[0].name == "Blue hoodie"
    assert candidates[0].category == "Tops"
    assert candidates[0].tags == ["Casual"]
    assert candidates[0].hebrew.name == "קפוצ׳ון כחול"
    assert candidates[0].hebrew.tags == ["יומיומי"]
    assert candidates[0].length == "regular"
    assert candidates[0].image_mime == "image/png"


def test_worn_garment_uses_gemini_image_editing() -> None:
    candidates = asyncio.run(
        analyze(
            _source(),
            is_video=False,
            ffmpeg_binary="ffmpeg",
            gemini=FakeGemini(worn=True),
        )
    )
    assert len(candidates) == 1
    assert candidates[0].image_mime == "image/png"


@pytest.mark.parametrize("worn", [False, True])
def test_review_image_is_generated_rather_than_the_source_crop(worn: bool) -> None:
    candidates = asyncio.run(
        analyze(_source(), is_video=False, ffmpeg_binary="ffmpeg", gemini=FakeGemini(worn))
    )
    preview = Image.open(io.BytesIO(base64.b64decode(candidates[0].image_base64)))
    assert preview.size == (100, 100)
    assert preview.getpixel((50, 50)) == (255, 0, 0, 255)


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("timeout"), ValueError("no image")])
def test_generation_failure_does_not_return_a_photographed_crop(failure: Exception) -> None:
    class FailedGemini(FakeGemini):
        async def create_catalog_image(self, image: bytes, name: str) -> bytes:
            raise failure

    with pytest.raises(CatalogImageError):
        asyncio.run(
            analyze(_source(), is_video=False, ffmpeg_binary="ffmpeg", gemini=FailedGemini())
        )


def test_invalid_generated_image_does_not_return_a_photographed_crop() -> None:
    class InvalidGemini(FakeGemini):
        async def create_catalog_image(self, image: bytes, name: str) -> bytes:
            return b"not an image"

    with pytest.raises(CatalogImageError):
        asyncio.run(
            analyze(_source(), is_video=False, ffmpeg_binary="ffmpeg", gemini=InvalidGemini())
        )


def test_catalog_cutout_keeps_transparency_through_review() -> None:
    class Remover:
        async def remove_background(self, image: bytes) -> bytes:
            generated = Image.open(io.BytesIO(image))
            assert generated.getpixel((50, 50))[:3] == (255, 0, 0)
            cutout = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
            cutout.putpixel((50, 50), (255, 255, 255, 255))
            output = io.BytesIO()
            cutout.save(output, format="PNG")
            return output.getvalue()

    candidates = asyncio.run(
        analyze(
            _source(),
            is_video=False,
            ffmpeg_binary="ffmpeg",
            gemini=FakeGemini(),
            background_remover=Remover(),
        )
    )
    preview = Image.open(io.BytesIO(base64.b64decode(candidates[0].image_base64)))
    assert preview.getpixel((0, 0))[3] == 0
    assert preview.getpixel((50, 50)) == (255, 255, 255, 255)


def test_failed_background_removal_returns_no_white_preview() -> None:
    class Remover:
        async def remove_background(self, image: bytes) -> bytes:
            raise ValueError("provider failure")

    with pytest.raises(CatalogImageError):
        asyncio.run(
            analyze(
                _source(),
                is_video=False,
                ffmpeg_binary="ffmpeg",
                gemini=FakeGemini(),
                background_remover=Remover(),
            )
        )


def test_large_scan_caps_paid_cutouts_and_fits_private_recovery_budget():
    import random

    from app.domains.wardrobe.schemas import AnalysisResponse

    output = io.BytesIO()
    Image.frombytes("RGBA", (1024, 1024), random.Random(0).randbytes(1024 * 1024 * 4)).save(
        output, format="PNG"
    )

    class ManyGarments(FakeGemini):
        calls = 0

        async def detect_garments(self, image):
            template = (await super().detect_garments(image))[0]
            return [{**template, "color": f"different-{number}"} for number in range(20)]

        async def create_catalog_image(self, image, name):
            self.calls += 1
            return output.getvalue()

    provider = ManyGarments()
    candidates = asyncio.run(
        analyze(_source(), is_video=False, ffmpeg_binary="ffmpeg", gemini=provider)
    )
    assert len(candidates) == 8
    assert provider.calls == 8
    assert all(len(base64.b64decode(c.image_base64)) <= 2_000_000 for c in candidates)
    assert len(AnalysisResponse(candidates=candidates).model_dump_json().encode()) < 24_000_000

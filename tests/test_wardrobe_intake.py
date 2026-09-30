import asyncio
import io

from PIL import Image

from app.domains.wardrobe.intake import analyze


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
                "worn": self.worn,
            },
            {"box_2d": [0, 0, 10, 10], "name": "noise"},
        ]

    async def isolate_worn_garment(self, image: bytes, name: str) -> bytes:
        assert self.worn and name == "Blue hoodie"
        output = io.BytesIO()
        Image.new("RGB", (100, 100), "blue").save(output, format="PNG")
        return output.getvalue()


class FakeBria:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed

    async def remove_background(self, image: bytes) -> bytes:
        assert self.allowed
        output = io.BytesIO()
        Image.open(io.BytesIO(image)).save(output, format="PNG")
        return output.getvalue()


def _source() -> bytes:
    source = io.BytesIO()
    Image.new("RGB", (140, 140), "blue").save(source, format="JPEG")
    return source.getvalue()


def test_flat_garment_becomes_reviewable_candidate() -> None:
    candidates = asyncio.run(
        analyze(
            _source(), is_video=False, ffmpeg_binary="ffmpeg", bria=FakeBria(), gemini=FakeGemini()
        )
    )
    assert len(candidates) == 1
    assert candidates[0].name == "Blue hoodie"
    assert candidates[0].category == "Tops"
    assert candidates[0].tags == ["Casual"]
    assert candidates[0].image_mime == "image/png"


def test_worn_garment_uses_gemini_image_editing() -> None:
    candidates = asyncio.run(
        analyze(
            _source(),
            is_video=False,
            ffmpeg_binary="ffmpeg",
            bria=FakeBria(allowed=False),
            gemini=FakeGemini(worn=True),
        )
    )
    assert len(candidates) == 1
    assert candidates[0].image_mime == "image/png"

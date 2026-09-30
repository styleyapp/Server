import asyncio
import base64
import io
from urllib.parse import urlparse

import httpx
from PIL import Image


class Bria:
    """Remove backgrounds with Bria's official Replicate model."""

    def __init__(self, client: httpx.AsyncClient, token: str) -> None:
        self.client = client
        self.token = token
        self.headers = {"Authorization": f"Bearer {token}"}

    async def remove_background(self, image: bytes) -> bytes:
        if not self.token:
            raise ValueError("Replicate API token is not configured")
        image_input = "data:image/jpeg;base64," + base64.b64encode(_small_jpeg(image)).decode()
        response = await self.client.post(
            "https://api.replicate.com/v1/models/bria/remove-background/predictions",
            headers={**self.headers, "Prefer": "wait=60", "Cancel-After": "90s"},
            json={"input": {"image": image_input, "preserve_alpha": True}},
            timeout=70,
        )
        response.raise_for_status()
        prediction = response.json()
        for attempt in range(15):
            output = prediction.get("output")
            if isinstance(output, str):
                if not _url(output, "replicate.delivery"):
                    raise ValueError("Replicate returned an invalid image URL")
                async with self.client.stream("GET", output, timeout=30) as result:
                    result.raise_for_status()
                    content = bytearray()
                    async for chunk in result.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 10_000_000:
                            raise ValueError("Replicate image exceeds size limit")
                return bytes(content)
            if prediction.get("status") in {"failed", "canceled"}:
                raise ValueError("Replicate background removal failed")
            get_url = prediction.get("urls", {}).get("get")
            if attempt == 14 or not _url(get_url, "api.replicate.com"):
                raise ValueError("Replicate background removal timed out")
            await asyncio.sleep(2)
            status = await self.client.get(get_url, headers=self.headers, timeout=15)
            status.raise_for_status()
            prediction = status.json()
        raise ValueError("Replicate background removal timed out")


def _url(value: object, host: str) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and parsed.hostname == host


def _small_jpeg(image: bytes) -> bytes:
    if len(image) <= 256_000:
        return image
    garment = Image.open(io.BytesIO(image)).convert("RGB")
    for edge in (1024, 768, 512):
        garment.thumbnail((edge, edge))
        for quality in (82, 70, 58):
            output = io.BytesIO()
            garment.save(output, format="JPEG", quality=quality, optimize=True)
            if output.tell() <= 256_000:
                return output.getvalue()
    raise ValueError("Garment image could not be sized for Replicate")

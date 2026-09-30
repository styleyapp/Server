import asyncio
import io

import httpx
import pytest
from PIL import Image

from app.integrations.ai.bria import Bria


def _jpeg() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (80, 80), "blue").save(output, format="JPEG")
    return output.getvalue()


def test_bria_submits_garment_and_downloads_result() -> None:
    seen = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            assert request.headers["authorization"] == "Bearer private-token"
            assert request.headers["prefer"] == "wait=60"
            assert b"data:image/jpeg;base64," in request.content
            return httpx.Response(200, json={"output": "https://replicate.delivery/out.png"})
        return httpx.Response(200, content=b"transparent-png")

    async def run() -> bytes:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await Bria(client, "private-token").remove_background(_jpeg())

    assert asyncio.run(run()) == b"transparent-png"
    assert [request.method for request in seen] == ["POST", "GET"]
    assert "authorization" not in seen[1].headers


def test_bria_rejects_untrusted_output_url() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"output": "https://attacker.example/out.png"})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            await Bria(client, "private-token").remove_background(_jpeg())

    with pytest.raises(ValueError, match="invalid image URL"):
        asyncio.run(run())

import asyncio
import base64
import json

import httpx
import pytest
from google.oauth2.credentials import Credentials

from app.integrations.ai.gemini import Gemini


def test_garment_detection_uses_structured_vertex_output() -> None:
    item = {
        "box_2d": [50, 100, 900, 800],
        "name": "Blue hoodie",
        "category": "Tops",
        "type": "Hoodie",
        "color": "Blue",
        "season": "Winter",
        "tags": ["Casual"],
        "worn": False,
    }

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path.endswith("models/gemini-2.5-flash:generateContent")
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        assert body["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 0
        assert (
            body["contents"][0]["parts"][1]["inlineData"]["data"]
            == base64.b64encode(b"source").decode()
        )
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": json.dumps({"items": [item]})}]}}]
            },
        )

    async def run() -> list[dict]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            gemini = Gemini(
                client,
                "styley-project",
                credentials_factory=lambda: Credentials(token="test-token"),
            )
            return await gemini.detect_garments(b"source")

    assert asyncio.run(run()) == [item]


def test_garment_detection_rejects_malformed_provider_output() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "not JSON"}]}}]}
        )

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            gemini = Gemini(
                client,
                "styley-project",
                credentials_factory=lambda: Credentials(token="test-token"),
            )
            await gemini.detect_garments(b"source")

    with pytest.raises(httpx.ProtocolError):
        asyncio.run(run())


def test_worn_garment_uses_vertex_ai_with_cloud_credentials() -> None:
    edited = b"edited-image"

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.host == "aiplatform.googleapis.com"
        assert request.url.path == (
            "/v1/projects/styley-project/locations/global/publishers/google/"
            "models/gemini-3.1-flash-image:generateContent"
        )
        assert request.headers["Authorization"] == "Bearer test-token"
        assert body["contents"][0]["parts"][1]["inlineData"] == {
            "mimeType": "image/jpeg",
            "data": base64.b64encode(b"source").decode(),
        }
        assert body["generationConfig"]["responseModalities"] == ["TEXT", "IMAGE"]
        assert body["generationConfig"]["imageConfig"]["imageSize"] == "1K"
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "Here is the isolated garment."},
                                {"inlineData": {"data": base64.b64encode(edited).decode()}},
                            ]
                        },
                    }
                ]
            },
        )

    async def run() -> bytes:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            gemini = Gemini(
                client,
                "styley-project",
                credentials_factory=lambda: Credentials(token="test-token"),
            )
            return await gemini.isolate_worn_garment(b"source", "hoodie")

    assert asyncio.run(run()) == edited

import asyncio

import httpx

from app.core.config import Settings
from app.integrations.supabase.wardrobe import SupabaseWardrobe


def test_secret_key_is_sent_only_as_apikey() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["apikey"] == "sb_secret_test"
        assert "authorization" not in request.headers
        return httpx.Response(200, json=[])

    async def run() -> None:
        settings = Settings(
            "https://example.supabase.co",
            "sb_publishable_test",
            "sb_secret_test",
            "styley-project",
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            assert await SupabaseWardrobe(client, settings).list_items("owner") == []

    asyncio.run(run())

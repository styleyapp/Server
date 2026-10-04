import asyncio
import json
from types import SimpleNamespace

import httpx

from app.domains.wardrobe.schemas import UpdateItem
from app.integrations.supabase.wardrobe import SupabaseWardrobe

_SETTINGS = SimpleNamespace(supabase_url="https://supabase.test", supabase_secret_key="private")


def test_favorite_update_is_owned_and_changes_only_favorite_state() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        assert request.url.params["user_id"] == "eq.owner"
        assert request.url.params["id"] == "eq.item"
        assert json.loads(request.content) == {"is_favorite": True}
        return httpx.Response(200, json=[])

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            assert await SupabaseWardrobe(client, _SETTINGS).favorite("owner", "item", True) is None

    asyncio.run(run())


def test_legacy_edit_preserves_unsupplied_translation_and_length() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.params["user_id"] == "eq.owner"
        assert json.loads(request.content) == {"name": "New name"}
        return httpx.Response(200, json=[])

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            await SupabaseWardrobe(client, _SETTINGS).update(
                "owner", "item", UpdateItem(name="New name")
            )

    asyncio.run(run())


def test_delete_other_owners_item_never_touches_storage() -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.params["user_id"] == "eq.owner"
        return httpx.Response(200, json=[])

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            assert not await SupabaseWardrobe(client, _SETTINGS).delete("owner", "other-item")

    asyncio.run(run())
    assert len(requests) == 1


def test_delete_cleans_only_the_image_from_the_owned_deleted_row() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("wardrobe_items"):
            assert request.url.params["user_id"] == "eq.owner"
            return httpx.Response(200, json=[{"image_path": "owner/candidate.png"}])
        assert request.method == "DELETE"
        assert json.loads(request.content) == {"prefixes": ["owner/candidate.png"]}
        return httpx.Response(200, json=[])

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            assert await SupabaseWardrobe(client, _SETTINGS).delete("owner", "item")

    asyncio.run(run())


def test_deleted_item_stays_successful_if_private_image_cleanup_fails() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("wardrobe_items"):
            return httpx.Response(200, json=[{"image_path": "owner/candidate.png"}])
        return httpx.Response(503)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            assert await SupabaseWardrobe(client, _SETTINGS).delete("owner", "item")

    asyncio.run(run())

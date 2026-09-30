import asyncio

import httpx

from app.api.wardrobe import repository
from app.main import app


class FakeWardrobe:
    async def user_id(self, token: str) -> str | None:
        return "owner" if token == "valid" else None

    async def list_items(self, user_id: str) -> list:
        assert user_id == "owner"
        return []

    async def update(self, user_id: str, item_id: str, item: object) -> None:
        assert user_id == "owner"
        return None


async def _request(method: str, path: str, token: str | None = None) -> httpx.Response:
    app.dependency_overrides[repository] = lambda: FakeWardrobe()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            return await client.request(method, path, headers=headers)
    finally:
        app.dependency_overrides.clear()


def test_wardrobe_requires_a_valid_session() -> None:
    assert asyncio.run(_request("GET", "/v1/wardrobe/items")).status_code == 401
    assert asyncio.run(_request("GET", "/v1/wardrobe/items", "expired")).status_code == 401
    assert asyncio.run(_request("GET", "/v1/wardrobe/items", "valid")).json() == []


def test_update_only_looks_for_an_item_owned_by_the_session_user() -> None:
    payload = {"name": "Blue hoodie"}
    app.dependency_overrides[repository] = lambda: FakeWardrobe()

    async def request() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.patch(
                "/v1/wardrobe/items/other-item",
                json=payload,
                headers={"Authorization": "Bearer valid"},
            )

    try:
        assert asyncio.run(request()).status_code == 404
    finally:
        app.dependency_overrides.clear()

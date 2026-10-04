import asyncio
from types import SimpleNamespace

import httpx
import pytest

import app.api.wardrobe as wardrobe_api
from app.api.wardrobe import repository
from app.domains.wardrobe.intake import CatalogImageError
from app.main import app


class FakeWardrobe:
    async def user_id(self, token: str) -> str | None:
        return "owner" if token == "valid" else None

    async def list_items(self, user_id: str) -> list:
        assert user_id == "owner"
        return []

    async def delete(self, user_id: str, item_id: str) -> bool:
        assert user_id == "owner"
        return False

    async def favorite(self, user_id: str, item_id: str, value: bool) -> None:
        assert user_id == "owner"
        return None

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


def test_catalog_failure_returns_a_recoverable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failed_analysis(*args: object, **kwargs: object) -> list:
        raise CatalogImageError("private provider failure")

    monkeypatch.setattr(wardrobe_api, "analyze", failed_analysis)
    monkeypatch.setattr(
        app.state,
        "settings",
        SimpleNamespace(
            ffmpeg_binary="ffmpeg",
            google_cloud_project="test",
            google_cloud_location="global",
            replicate_api_token="test",
        ),
        raising=False,
    )
    monkeypatch.setattr(app.state, "http", object(), raising=False)
    app.dependency_overrides[repository] = lambda: FakeWardrobe()

    async def request() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.post(
                "/v1/wardrobe/analyze",
                files={"file": ("shirt.jpg", b"photo", "image/jpeg")},
                headers={"Authorization": "Bearer valid"},
            )

    try:
        response = asyncio.run(request())
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "provider_unavailable"
        assert "private provider failure" not in response.text
        assert "candidates" not in response.json()
    finally:
        app.dependency_overrides.clear()


def test_delete_is_authenticated_and_idempotent() -> None:
    assert asyncio.run(_request("DELETE", "/v1/wardrobe/items/item")).status_code == 401
    assert asyncio.run(_request("DELETE", "/v1/wardrobe/items/item", "valid")).status_code == 204
    assert asyncio.run(_request("DELETE", "/v1/wardrobe/items/item", "valid")).status_code == 204


def test_favorite_of_non_owned_item_returns_not_found() -> None:
    app.dependency_overrides[repository] = lambda: FakeWardrobe()

    async def request() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.patch(
                "/v1/wardrobe/items/other/favorite",
                json={"is_favorite": True},
                headers={"Authorization": "Bearer valid"},
            )

    try:
        assert asyncio.run(request()).status_code == 404
    finally:
        app.dependency_overrides.clear()

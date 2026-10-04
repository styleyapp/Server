import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

import app.api.wardrobe as wardrobe
from app.api.account import lifecycle
from app.api.dependencies import repository
from app.domains.outfits.schemas import OutfitFailure
from app.domains.wardrobe.schemas import AnalysisResponse
from app.integrations.supabase.lifecycle import SupabaseLifecycle
from app.integrations.supabase.scans import SupabaseScans
from app.main import app, request_logger


@pytest.fixture(autouse=True)
def isolated_app(monkeypatch):
    monkeypatch.setattr(app.state, "http", object(), raising=False)
    monkeypatch.setattr(
        app.state,
        "settings",
        SimpleNamespace(allowed_web_origins=("https://app.styley.test",)),
        raising=False,
    )
    yield
    app.dependency_overrides.clear()


async def request(method, path, **kwargs):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.request(method, path, **kwargs)


class Owner:
    async def user_id(self, token):
        return "owner" if token == "valid" else None

    async def page(self, user, limit, cursor):
        assert (user, limit, cursor) == ("owner", 50, None)
        return [], "next-page"


@pytest.mark.parametrize(
    "status,expected",
    [
        ("ready", 200),
        ("generation_in_progress", 409),
        ("rate_limited", 429),
        ("conflict", 409),
        ("expired", 410),
    ],
)
def test_scan_replay_and_rejections_never_call_paid_provider(monkeypatch, status, expected):
    calls = []

    class Scans:
        def __init__(self, *_):
            pass

        async def claim(self, user, identity, content):
            assert user == "owner" and content == b"photo"
            calls.append("claim")
            return {"status": status}

        async def recover(self, user, identity):
            calls.append("recover")
            return AnalysisResponse(candidates=[])

    async def forbidden(*args, **kwargs):
        pytest.fail("Paid provider called on a replay or rejected request")

    app.dependency_overrides[repository] = Owner
    monkeypatch.setattr(wardrobe, "SupabaseScans", Scans)
    monkeypatch.setattr(wardrobe, "analyze", forbidden)
    response = asyncio.run(
        request(
            "POST",
            "/v1/wardrobe/analyze",
            files={"file": ("photo.jpg", b"photo", "image/jpeg")},
            headers={"Authorization": "Bearer valid", "X-Request-ID": str(uuid4())},
        )
    )
    assert response.status_code == expected
    assert calls == (["claim", "recover"] if status == "ready" else ["claim"])


def test_pagination_cursor_is_exposed_only_to_allowed_web_origin():
    app.dependency_overrides[repository] = Owner
    response = asyncio.run(
        request(
            "GET",
            "/v1/wardrobe/items?limit=50",
            headers={"Authorization": "Bearer valid", "Origin": "https://app.styley.test"},
        )
    )
    assert response.json() == []
    assert response.headers["x-next-cursor"] == "next-page"
    assert response.headers["access-control-allow-origin"] == "https://app.styley.test"
    assert "X-Next-Cursor" in response.headers["access-control-expose-headers"]
    denied = asyncio.run(
        request(
            "OPTIONS",
            "/v1/wardrobe/analyze",
            headers={"Origin": "https://other.test", "Access-Control-Request-Method": "POST"},
        )
    )
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


@pytest.mark.parametrize(
    "body,token,status",
    [
        ({"confirmation": "DELETE"}, None, 401),
        ({"confirmation": "WRONG"}, "valid", 422),
        ({"confirmation": "DELETE", "user_id": "other"}, "valid", 422),
        ({"confirmation": "DELETE"}, "valid", 202),
    ],
)
def test_deletion_is_confirmed_and_session_owned(body, token, status):
    queued = []

    class Store:
        async def authenticated_user(self, bearer):
            return "owner" if bearer == "valid" else None

        async def enqueue_deletion(self, user):
            queued.append(user)

    app.dependency_overrides[lifecycle] = Store
    headers = {"Authorization": "Bearer " + token} if token else {}
    result = asyncio.run(request("DELETE", "/v1/me/account", json=body, headers=headers))
    assert result.status_code == status
    assert queued == (["owner"] if status == 202 else [])


def test_declared_and_chunked_body_limits():
    response = asyncio.run(
        request(
            "DELETE",
            "/v1/me/account",
            content=b"small",
            headers={"Content-Length": str(7 * 1024 * 1024)},
        )
    )
    assert response.status_code == 413

    async def chunks():
        for _ in range(7):
            yield b"x" * (1024 * 1024)

    response = asyncio.run(
        request(
            "DELETE",
            "/v1/me/account",
            content=chunks(),
            headers={"Content-Type": "application/json"},
        )
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"


def test_safe_observability_uses_route_templates_not_private_request_data(caplog):
    request_logger.addHandler(caplog.handler)
    try:
        asyncio.run(
            request(
                "GET",
                "/missing-private-email?token=secret",
                headers={"Authorization": "Bearer private-session"},
            )
        )
        entry = json.loads(caplog.records[-1].message)
        assert set(entry) == {"trace_id", "route", "method", "status", "duration_ms"}
        assert entry["route"] == "unmatched"
        assert "private" not in caplog.text and "secret" not in caplog.text
    finally:
        request_logger.removeHandler(caplog.handler)


def test_stale_scan_worker_cannot_publish_over_a_new_lease():
    user, identity, old, new = map(str, [uuid4(), uuid4(), uuid4(), uuid4()])
    visited = []

    def handler(req):
        visited.append(req)
        if req.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "state": "ready",
                        "lease_token": new,
                        "expires_at": "2099-01-01T00:00:00+00:00",
                    }
                ],
            )
        return httpx.Response(200, json={})

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            store = SupabaseScans(
                client,
                SimpleNamespace(
                    supabase_url="https://supabase.test", supabase_secret_key="service"
                ),
            )
            with pytest.raises(OutfitFailure, match="generation_in_progress"):
                await store.finish(user, identity, old, AnalysisResponse(candidates=[]))

    asyncio.run(check())
    assert old in visited[0].url.path
    assert visited[1].url.params["lease_token"] == "eq." + old
    assert visited[1].url.params["user_id"] == "eq." + user


def test_one_cleanup_failure_does_not_starve_other_accounts():
    first, second = str(uuid4()), str(uuid4())
    deleted = []

    def handler(req):
        if req.url.path == "/rest/v1/account_deletions" and req.method == "GET":
            assert req.url.params["order"].startswith("last_attempt_at")
            return httpx.Response(
                200, json=[{"user_id": first, "attempts": 0}, {"user_id": second, "attempts": 0}]
            )
        if req.url.path == "/rest/v1/rpc/account_storage_paths":
            if json.loads(req.content)["p_user"] == first:
                return httpx.Response(503)
            return httpx.Response(200, json=[])
        if req.url.path.startswith("/auth/v1/admin/users/"):
            deleted.append(req.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json=[])

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await SupabaseLifecycle(
                client,
                SimpleNamespace(
                    supabase_url="https://supabase.test", supabase_secret_key="service"
                ),
            ).maintain()
            assert result["accounts_completed"] == 1
            assert result["accounts_retrying"] == 1

    asyncio.run(check())
    assert deleted == [second]


def test_cleanup_removes_nested_paths_and_refuses_foreign_owners():
    owner = str(uuid4())
    removed = []
    calls = 0

    def handler(req):
        nonlocal calls
        if req.url.path == "/rest/v1/rpc/account_storage_paths":
            calls += 1
            return httpx.Response(
                200, json=[{"name": owner + "/older/nested/image.png"}] if calls == 1 else []
            )
        removed.extend(json.loads(req.content)["prefixes"])
        return httpx.Response(200, json=[])

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            store = SupabaseLifecycle(
                client,
                SimpleNamespace(
                    supabase_url="https://supabase.test", supabase_secret_key="service"
                ),
            )
            assert await store.clear_prefix("wardrobe-items", owner)

    asyncio.run(check())
    assert removed == [owner + "/older/nested/image.png"]

    async def foreign():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=[{"name": "other/private.png"}])
            )
        ) as client:
            store = SupabaseLifecycle(
                client,
                SimpleNamespace(
                    supabase_url="https://supabase.test", supabase_secret_key="service"
                ),
            )
            with pytest.raises(ValueError, match="owner"):
                await store.clear_prefix("wardrobe-items", owner)

    asyncio.run(foreign())

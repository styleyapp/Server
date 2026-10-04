import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from app.api.dependencies import repository
from app.api.preferences import preferences_repository
from app.core.config import Settings
from app.domains.users.preferences import (
    Preferences,
    PreferencesConflict,
    PreferencesSnapshot,
    SavePreferences,
)
from app.integrations.supabase.preferences import SupabasePreferences
from app.main import app


class Identity:
    async def user_id(self, token: str) -> str | None:
        return "trusted-owner" if token == "valid" else None


class FakePreferences:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.conflict = False

    async def get(self, owner: str) -> PreferencesSnapshot:
        self.calls.append(owner)
        return PreferencesSnapshot(preferences=Preferences(top_fit="relaxed"), revision=3)

    async def save(self, owner: str, request: SavePreferences) -> PreferencesSnapshot:
        self.calls.append(owner)
        if self.conflict:
            raise PreferencesConflict()
        return PreferencesSnapshot(preferences=request.preferences, revision=4)


def request(
    method: str,
    payload: dict | None = None,
    token: str | None = "valid",
    repo: FakePreferences | None = None,
) -> httpx.Response:
    app.dependency_overrides[repository] = lambda: Identity()
    app.dependency_overrides[preferences_repository] = lambda: repo or FakePreferences()

    async def run() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            return await client.request(
                method,
                "/v1/me/preferences",
                json=payload,
                headers={"Authorization": f"Bearer {token}"} if token else {},
            )

    try:
        return asyncio.run(run())
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("token", [None, "expired", ""])
def test_signed_out_requests_never_access_preferences(token: str | None) -> None:
    repo = FakePreferences()
    assert request("GET", token=token, repo=repo).status_code == 401
    assert request("PUT", {}, token=token, repo=repo).status_code == 401
    assert repo.calls == []


def test_identity_is_derived_from_the_verified_session() -> None:
    repo = FakePreferences()
    response = request("GET", repo=repo)
    assert response.status_code == 200
    assert response.json()["revision"] == 3
    assert repo.calls == ["trusted-owner"]
    assert "user_id" not in response.text


@pytest.mark.parametrize(
    "invalid",
    [
        {"user_id": "another-user"},
        {"age": 12},
        {"age": 66},
        {"age": True},
        {"age": "25"},
        {"top_fit": "unknown"},
        {"locale": "fr"},
        {"closet_categories": ["men", "men"]},
        {"closet_categories": ["other"]},
    ],
)
def test_invalid_or_privileged_fields_are_rejected(invalid: dict) -> None:
    payload = {"preferences": {**Preferences().model_dump(), **invalid}, "expected_revision": 3}
    assert request("PUT", payload).status_code == 422


@pytest.mark.parametrize("revision", [-1, True, "3", None])
def test_revision_is_explicit_and_strict(revision: object) -> None:
    payload = {"preferences": Preferences().model_dump(), "expected_revision": revision}
    assert request("PUT", payload).status_code == 422


def test_missing_revision_and_body_identity_are_rejected() -> None:
    assert request("PUT", {"preferences": Preferences().model_dump()}).status_code == 422
    assert (
        request(
            "PUT",
            {
                "preferences": Preferences().model_dump(),
                "expected_revision": 3,
                "user_id": "someone",
            },
        ).status_code
        == 422
    )


def test_conflicts_have_a_safe_stable_error_contract() -> None:
    repo = FakePreferences()
    repo.conflict = True
    response = request(
        "PUT", {"preferences": Preferences().model_dump(), "expected_revision": 3}, repo=repo
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert response.json()["error"]["trace_id"] == response.headers["X-Trace-ID"]


def test_nullable_age_and_canonical_categories_round_trip() -> None:
    response = request(
        "PUT",
        {
            "preferences": {"age": None, "closet_categories": ["women", "men"], "locale": "en"},
            "expected_revision": 3,
        },
    )
    assert response.status_code == 200
    assert response.json()["preferences"]["age"] is None
    assert response.json()["preferences"]["closet_categories"] == ["men", "women"]


class Store:
    """Emulates PostgREST compare-and-set and uniqueness at its HTTP boundary."""

    def __init__(self, row: dict | None = None) -> None:
        self.row = row
        self.requests: list[httpx.Request] = []
        self.race: dict | None = None
        self.fail = False

    def respond(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        assert req.headers["apikey"] == "sb_secret_test"
        assert "authorization" not in req.headers
        if self.fail:
            return httpx.Response(503, json={"secret": "internal diagnostic"})
        if req.method == "GET":
            assert req.url.params["user_id"] == "eq.owner"
            return httpx.Response(200, json=[self.row] if self.row else [])
        payload = json.loads(req.content)
        if self.race is not None:
            self.row = self.race
            self.race = None
        if req.method == "POST":
            assert payload["user_id"] == "owner"
            assert req.url.params["on_conflict"] == "user_id"
            assert "ignore-duplicates" in req.headers["prefer"]
            if self.row:
                return httpx.Response(201, json=[])
        else:
            assert req.url.params["user_id"] == "eq.owner"
            if not self.row or req.url.params["revision"] != f"eq.{self.row['revision']}":
                return httpx.Response(200, json=[])
        self.row = {key: payload[key] for key in ["preferences", "revision"]}
        return httpx.Response(200, json=[self.row])


def run_store(store: Store, preferences: Preferences, revision: int) -> PreferencesSnapshot:
    async def run() -> PreferencesSnapshot:
        async with httpx.AsyncClient(transport=httpx.MockTransport(store.respond)) as client:
            repo = SupabasePreferences(
                client,
                Settings(
                    "https://test.supabase.co", "sb_publishable_test", "sb_secret_test", "test"
                ),
            )
            return await repo.save(
                "owner", SavePreferences(preferences=preferences, expected_revision=revision)
            )

    return asyncio.run(run())


def test_first_save_and_lost_response_retry_do_not_duplicate_or_increment() -> None:
    store = Store()
    prefs = Preferences(top_fit="relaxed")
    assert run_store(store, prefs, 0).revision == 1
    assert run_store(store, prefs, 0).revision == 1
    assert [req.method for req in store.requests].count("POST") == 1


def test_update_is_owner_and_revision_scoped() -> None:
    store = Store({"preferences": Preferences().model_dump(), "revision": 4})
    assert run_store(store, Preferences(pants_fit="baggy"), 4).revision == 5
    assert store.requests[-1].url.params["revision"] == "eq.4"


@pytest.mark.parametrize(
    "initial", [None, {"preferences": Preferences().model_dump(), "revision": 1}]
)
def test_concurrent_create_or_update_cannot_overwrite_the_winner(initial: dict | None) -> None:
    store = Store(initial)
    winner = {"preferences": Preferences(top_fit="slim").model_dump(), "revision": 2}
    store.race = winner
    with pytest.raises(PreferencesConflict):
        run_store(store, Preferences(top_fit="relaxed"), 1 if initial else 0)
    assert store.row == winner


def test_concurrent_identical_write_returns_the_committed_snapshot() -> None:
    store = Store()
    prefs = Preferences(top_fit="slim")
    store.race = {"preferences": prefs.model_dump(), "revision": 1}
    assert run_store(store, prefs, 0).revision == 1


def test_stale_save_does_not_issue_a_write() -> None:
    store = Store({"preferences": Preferences().model_dump(), "revision": 4})
    with pytest.raises(PreferencesConflict):
        run_store(store, Preferences(top_fit="slim"), 3)
    assert [req.method for req in store.requests] == ["GET"]


def test_upstream_failure_cannot_be_reported_as_a_successful_save() -> None:
    store = Store()
    store.fail = True
    with pytest.raises(httpx.HTTPStatusError):
        run_store(store, Preferences(), 0)


def test_duplicate_categories_are_not_silently_dropped() -> None:
    with pytest.raises(ValidationError):
        Preferences(closet_categories=["women", "women"])

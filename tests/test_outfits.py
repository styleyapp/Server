import asyncio
import json
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from app.api.dependencies import repository
from app.api.outfits import outfit_preferences, outfit_proposer, outfit_repository
from app.domains.outfits.generation import generate
from app.domains.outfits.schemas import GenerateOutfit, OutfitFailure, OutfitProposal
from app.integrations.ai.gemini import Gemini
from app.integrations.supabase.outfits import SupabaseOutfits
from app.main import app

OWNER = "00000000-0000-0000-0000-000000000001"
OUTFIT = "20000000-0000-0000-0000-000000000001"
FIRST = "10000000-0000-0000-0000-000000000001"
SECOND = "10000000-0000-0000-0000-000000000002"
PAYLOAD = {"request_id": OUTFIT, "context": {"occasion": "Work", "mood": "Relaxed", "notes": ""}}
PROPOSAL = {
    "complete": True,
    "title_en": "Owned pair",
    "title_he": "לוק",
    "reason_en": "A simple pair",
    "reason_he": "שילוב",
    "pieces": [{"slot": "top", "garment_id": FIRST}, {"slot": "bottom", "garment_id": SECOND}],
}


class Store:
    def __init__(self, status="claimed"):
        self.status = status
        self.calls = []
        self.failed = None
        self.completion = "ready"
        self.rows = [{"id": FIRST}, {"id": SECOND}]

    async def claim(self, user, request, digest):
        self.calls.append(("claim", user, digest))
        assert len(digest) == 64
        return {"status": self.status, "id": OUTFIT, "lease_token": OUTFIT}

    async def candidates(self, user):
        self.calls.append(("wardrobe", user))
        return self.rows

    async def complete(self, user, identity, lease, proposal):
        self.calls.append(("complete", user, identity, lease, proposal))
        return self.completion

    async def fail(self, user, identity, lease, code):
        self.failed = code

    async def get(self, user, identity):
        self.calls.append(("get", user, identity))
        return "persisted-outfit"


class Proposer:
    def __init__(self, response=None, error=None):
        self.response = response or PROPOSAL
        self.error = error
        self.calls = 0

    async def propose_outfit(self, wardrobe, context, preferences):
        self.calls += 1
        assert set(preferences) == {"top_fit", "pants_fit"}
        if self.error:
            raise self.error
        return self.response


class Preferences:
    async def get(self, user):
        return SimpleNamespace(preferences=SimpleNamespace(top_fit="regular", pants_fit="straight"))


def run(store, proposer=None, payload=None):
    return asyncio.run(
        generate(
            OWNER,
            GenerateOutfit.model_validate(payload or PAYLOAD),
            store,
            Preferences(),
            proposer or Proposer(),
        )
    )


def test_completed_request_retry_never_calls_ai_or_wardrobe():
    store = Store("ready")
    proposer = Proposer()
    assert run(store, proposer) == "persisted-outfit"
    assert proposer.calls == 0
    assert [c[0] for c in store.calls] == ["claim", "get"]


@pytest.mark.parametrize("status", ["generation_in_progress", "conflict", "rate_limited"])
def test_rejected_claims_never_bill_provider(status):
    proposer = Proposer()
    with pytest.raises(OutfitFailure) as caught:
        run(Store(status), proposer)
    assert caught.value.code == status and proposer.calls == 0


def test_generated_pieces_are_validated_then_persisted_with_trusted_owner():
    store = Store()
    assert run(store) == "persisted-outfit"
    assert [c[0] for c in store.calls] == ["claim", "wardrobe", "complete", "get"]
    assert all(c[1] == OWNER for c in store.calls)


def test_foreign_garment_in_model_output_is_never_persisted():
    store = Store()
    proposal = {
        **PROPOSAL,
        "pieces": [PROPOSAL["pieces"][0], {"slot": "bottom", "garment_id": OUTFIT}],
    }
    with pytest.raises(OutfitFailure) as caught:
        run(store, Proposer(proposal))
    assert caught.value.code == "invalid_proposal"
    assert store.failed == "invalid_proposal"
    assert "complete" not in [c[0] for c in store.calls]


@pytest.mark.parametrize(
    "pieces",
    [
        [],
        [PROPOSAL["pieces"][0]],
        [PROPOSAL["pieces"][0], PROPOSAL["pieces"][0]],
        [{"slot": "unknown", "garment_id": FIRST}],
    ],
)
def test_invalid_compositions_are_rejected(pieces):
    with pytest.raises(ValidationError):
        OutfitProposal.model_validate({**PROPOSAL, "pieces": pieces})


def test_empty_wardrobe_does_not_bill_ai():
    store = Store()
    store.rows = []
    proposer = Proposer()
    with pytest.raises(OutfitFailure) as caught:
        run(store, proposer)
    assert caught.value.code == "insufficient_wardrobe" and proposer.calls == 0


def test_provider_detected_incomplete_wardrobe_has_actionable_failure():
    store = Store()
    with pytest.raises(OutfitFailure) as caught:
        run(store, Proposer({**PROPOSAL, "complete": False, "pieces": []}))
    assert caught.value.code == store.failed == "insufficient_wardrobe"


@pytest.mark.parametrize("error", [TimeoutError(), httpx.ProtocolError("private provider detail")])
def test_provider_failures_release_lease_and_do_not_expose_diagnostics(error):
    store = Store()
    with pytest.raises(OutfitFailure) as caught:
        run(store, Proposer(error=error))
    assert caught.value.code == store.failed == "provider_unavailable"
    assert "complete" not in [c[0] for c in store.calls]


def test_garment_deleted_during_generation_is_not_reported_as_success():
    store = Store()
    store.completion = "wardrobe_changed"
    with pytest.raises(OutfitFailure) as caught:
        run(store)
    assert caught.value.code == "wardrobe_changed"
    assert "get" not in [c[0] for c in store.calls]


@pytest.mark.parametrize(
    "payload",
    [
        {**PAYLOAD, "user_id": OWNER},
        {**PAYLOAD, "context": {"notes": "x" * 501}},
        {**PAYLOAD, "request_id": "not-a-uuid"},
        {**PAYLOAD, "context": {"occasion": "   "}},
    ],
)
def test_identity_and_unbounded_inputs_are_rejected(payload):
    with pytest.raises(ValidationError):
        GenerateOutfit.model_validate(payload)


def route(method, path, token="valid", store=None, payload=None):
    class Identity:
        async def user_id(self, value):
            return OWNER if value == "valid" else None

    app.dependency_overrides[repository] = lambda: Identity()
    app.dependency_overrides[outfit_repository] = lambda: store or Store()
    app.dependency_overrides[outfit_preferences] = Preferences
    app.dependency_overrides[outfit_proposer] = Proposer

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            return await client.request(
                method,
                path,
                json=payload,
                headers={"Authorization": "Bearer " + token} if token else {},
            )

    try:
        return asyncio.run(request())
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/v1/outfits/generate"),
        ("GET", "/v1/outfits"),
        ("GET", "/v1/outfits/" + OUTFIT),
        ("PUT", "/v1/outfits/" + OUTFIT + "/save"),
    ],
)
def test_outfit_endpoints_require_valid_session(method, path):
    assert route(method, path, token="expired", payload=PAYLOAD).status_code == 401
    assert route(method, path, token=None, payload=PAYLOAD).status_code == 401


def test_busy_route_has_stable_safe_error_and_trace():
    response = route(
        "POST", "/v1/outfits/generate", store=Store("generation_in_progress"), payload=PAYLOAD
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "generation_in_progress"
    assert response.json()["error"]["trace_id"] == response.headers["x-trace-id"]


def test_malformed_id_and_cursor_limits_are_rejected_before_query():
    assert route("GET", "/v1/outfits/not-a-uuid").status_code == 422
    assert route("GET", "/v1/outfits?limit=0").status_code == 422
    assert route("GET", "/v1/outfits?limit=51").status_code == 422


def test_adapter_owner_filters_atomic_save_and_missing_items():
    requests = []

    def respond(req):
        requests.append(req)
        assert req.headers["apikey"] == "private" and "authorization" not in req.headers
        if req.url.path.endswith("/rpc/save_outfit"):
            assert json.loads(req.content) == {"p_user": OWNER, "p_id": OUTFIT}
            return httpx.Response(200, json="wardrobe_changed")
        assert req.url.params["user_id"] == "eq." + OWNER
        return httpx.Response(200, json=[])

    async def request():
        settings = SimpleNamespace(supabase_url="https://supa.test", supabase_secret_key="private")
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            store = SupabaseOutfits(client, settings)
            with pytest.raises(OutfitFailure) as e:
                await store.get(OWNER, UUID(OUTFIT))
            assert e.value.code == "not_found"
            with pytest.raises(OutfitFailure) as e:
                await store.save(OWNER, UUID(OUTFIT))
            assert e.value.code == "wardrobe_changed"
            assert (await store.list_saved(OWNER, 20, None)).items == []
            for cursor in ["invalid", "e30", "W10"]:
                with pytest.raises(OutfitFailure) as e:
                    await store.list_saved(OWNER, 20, cursor)
                assert e.value.code == "invalid_cursor"

    asyncio.run(request())
    assert len(requests) == 3


def test_model_request_is_bounded_metadata_only_and_uses_enum_ids():
    def respond(req):
        payload = json.loads(req.content)
        assert payload["generationConfig"]["maxOutputTokens"] == 1500
        assert payload["systemInstruction"]
        values = payload["generationConfig"]["responseSchema"]["properties"]["pieces"]["items"]
        assert values["properties"]["garment_id"]["enum"] == [FIRST, SECOND]
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(PROPOSAL)}]}}]}
        )

    async def request():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            credentials = SimpleNamespace(valid=True, token="test-token")
            ai = Gemini(client, "project", credentials_factory=lambda: credentials)
            assert await ai.propose_outfit([{"id": FIRST}, {"id": SECOND}], {}, {}) == PROPOSAL

    asyncio.run(request())


def test_known_garment_roles_cannot_be_invented_by_model():
    store = Store()
    store.rows = [
        {"id": FIRST, "category": "Tops", "type": "Shirt"},
        {"id": SECOND, "category": "Tops", "type": "Shirt"},
    ]
    with pytest.raises(OutfitFailure) as caught:
        run(store)
    assert caught.value.code == "invalid_proposal"
    assert "complete" not in [c[0] for c in store.calls]


@pytest.mark.parametrize("storage_fails", [False, True])
def test_live_metadata_is_current_deleted_pieces_are_marked_and_storage_outages_degrade(
    storage_fails,
):
    raw = {
        "id": OUTFIT,
        "request_id": OUTFIT,
        "context": PAYLOAD["context"],
        "title_en": "Outfit",
        "title_he": "לוק",
        "reason_en": "Pair",
        "reason_he": "שילוב",
        "created_at": "2026-10-04T10:00:00Z",
        "saved_at": "2026-10-04T10:01:00Z",
        "state": "ready",
        "outfit_pieces": [
            {
                "position": 0,
                "slot": "top",
                "garment_id": FIRST,
                "wardrobe_item_id": FIRST,
                "snapshot": {"name": "Original shirt"},
            },
            {
                "position": 1,
                "slot": "bottom",
                "garment_id": SECOND,
                "wardrobe_item_id": None,
                "snapshot": {"name": "Deleted pants"},
            },
        ],
    }

    def respond(req):
        if req.url.path.endswith("/outfits"):
            assert req.url.params["user_id"] == "eq." + OWNER
            return httpx.Response(200, json=[raw])
        if req.url.path.endswith("/wardrobe_items"):
            assert req.url.params["id"] == "in.(" + FIRST + ")"
            return httpx.Response(
                200,
                json=[
                    {
                        "id": FIRST,
                        "name": "Edited shirt",
                        "source": "photo",
                        "image_path": OWNER + "/first.png",
                    }
                ],
            )
        assert json.loads(req.content)["paths"] == [OWNER + "/first.png"]
        if storage_fails:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json=[
                {
                    "path": OWNER + "/first.png",
                    "signedURL": "/object/sign/wardrobe-items/first.png?token=test",
                }
            ],
        )

    async def request():
        settings = SimpleNamespace(supabase_url="https://supa.test", supabase_secret_key="private")
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result = await SupabaseOutfits(client, settings).get(OWNER, OUTFIT)
            assert result.pieces[0].item.name == "Edited shirt"
            assert result.pieces[0].snapshot.name == "Original shirt"
            assert result.pieces[1].item is None and not result.pieces[1].available
            assert result.pieces[1].snapshot.name == "Deleted pants"
            assert (result.pieces[0].item.image_url is None) == storage_fails

    asyncio.run(request())


def test_failure_rpc_accepts_postgrest_empty_void_response():
    def respond(request):
        assert request.url.path.endswith("/rpc/fail_outfit")
        assert json.loads(request.content)["p_user"] == OWNER
        return httpx.Response(204)

    async def request():
        settings = SimpleNamespace(supabase_url="https://supa.test", supabase_secret_key="private")
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            await SupabaseOutfits(client, settings).fail(
                OWNER, OUTFIT, OUTFIT, "provider_unavailable"
            )

    asyncio.run(request())

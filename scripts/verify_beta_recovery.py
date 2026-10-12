"""Opt-in live recovery test with disposable accounts and synthetic paid providers."""

import argparse
import asyncio
import base64
import io
import json
import secrets
import tempfile
import uuid
from pathlib import Path

import httpx
from PIL import Image

import app.api.wardrobe as wardrobe
from app.api.outfits import outfit_proposer
from app.core.config import Settings
from app.domains.wardrobe.schemas import Candidate
from app.integrations.supabase.lifecycle import SupabaseLifecycle
from app.main import app

STATE = Path(tempfile.gettempdir()) / "styley-beta-recovery-cleanup.json"


async def main():
    settings = Settings.from_env()
    assert settings.supabase_url == "https://psbxrnojkteritdifpls.supabase.co"
    admin = {"apikey": settings.supabase_secret_key}
    public = {"apikey": settings.supabase_publishable_key}
    users = []
    paths = []
    scanpaths = []
    tokens = []
    calls = 0

    def record():
        STATE.write_text(json.dumps({"users": users, "paths": paths, "scanpaths": scanpaths}))
        STATE.chmod(0o600)

    def check(r, status, label):
        if r.status_code != status:
            raise AssertionError(label + ": status " + str(r.status_code))
        print(label + ": passed", flush=True)
        return r.json() if r.content else None

    image = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(image, format="PNG")
    encoded = base64.b64encode(image.getvalue()).decode()
    candidates = [
        Candidate(
            id=str(uuid.uuid4()),
            name="Test shirt",
            category="Tops",
            type="Shirt",
            image_base64=encoded,
            source="photo",
        ),
        Candidate(
            id=str(uuid.uuid4()),
            name="Test trousers",
            category="Bottoms",
            type="Trousers",
            image_base64=encoded,
            source="photo",
        ),
    ]

    async def synthetic(*args, **kwargs):
        nonlocal calls
        calls += 1
        return candidates

    class Proposer:
        async def propose_outfit(self, rows, *args):
            return {
                "complete": True,
                "title_en": "Test outfit",
                "title_he": "לוק בדיקה",
                "reason_en": "Synthetic test composition",
                "reason_he": "הרכב בדיקה",
                "pieces": [
                    {"garment_id": row["id"], "slot": "top" if row["type"] == "Shirt" else "bottom"}
                    for row in rows
                ],
            }

    original = wardrobe.analyze
    wardrobe.analyze = synthetic
    app.dependency_overrides[outfit_proposer] = Proposer
    async with httpx.AsyncClient(timeout=25) as real:
        app.state.http = real
        app.state.settings = settings
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local", timeout=120
        ) as api:
            try:
                for _ in range(2):
                    email = f"styley-beta-test-{uuid.uuid4().hex}@example.invalid"
                    password = secrets.token_urlsafe(32)
                    r = await real.post(
                        settings.supabase_url + "/auth/v1/admin/users",
                        headers=admin,
                        json={"email": email, "password": password, "email_confirm": True},
                    )
                    r.raise_for_status()
                    users.append(r.json()["id"])
                    record()
                    r = await real.post(
                        settings.supabase_url + "/auth/v1/token?grant_type=password",
                        headers=public,
                        json={"email": email, "password": password},
                    )
                    r.raise_for_status()
                    tokens.append({"Authorization": "Bearer " + r.json()["access_token"]})
                identity = str(uuid.uuid4())
                headers = {**tokens[0], "X-Request-ID": identity}
                result = check(
                    await api.post(
                        "/v1/wardrobe/analyze",
                        headers=headers,
                        files={"file": ("test.jpg", b"synthetic-photo", "image/jpeg")},
                    ),
                    200,
                    "Authenticated scan persists a recoverable private result",
                )
                row = (
                    await real.get(
                        settings.supabase_url + "/rest/v1/scan_requests",
                        headers=admin,
                        params={"user_id": "eq." + users[0], "request_id": "eq." + identity},
                    )
                ).json()[0]
                scanpaths.append(users[0] + "/" + identity + "-" + row["lease_token"] + ".json")
                record()
                replay = check(
                    await api.post(
                        "/v1/wardrobe/analyze",
                        headers=headers,
                        files={"file": ("test.jpg", b"synthetic-photo", "image/jpeg")},
                    ),
                    200,
                    "Scan replay avoids another provider call",
                )
                assert replay == result and calls == 1
                recovered = check(
                    await api.get("/v1/wardrobe/scans/latest", headers=tokens[0]),
                    200,
                    "Reopen and resume last scan",
                )
                assert recovered == result
                check(
                    await api.get("/v1/wardrobe/scans/latest", headers=tokens[1]),
                    404,
                    "Other account cannot read scan",
                )
                for candidate in result["candidates"]:
                    paths.append(users[0] + "/" + candidate["id"] + ".png")
                    record()
                    payload = {**candidate, "candidate_id": candidate["id"]}
                    payload.pop("id")
                    first = check(
                        await api.post("/v1/wardrobe/items", headers=tokens[0], json=payload),
                        201,
                        "Save scanned garment",
                    )
                    second = check(
                        await api.post("/v1/wardrobe/items", headers=tokens[0], json=payload),
                        201,
                        "Retry garment save without duplication",
                    )
                    assert first["id"] == second["id"]
                page = await api.get("/v1/wardrobe/items?limit=1", headers=tokens[0])
                check(page, 200, "Read bounded wardrobe page")
                assert len(page.json()) == 1 and page.headers.get("x-next-cursor")
                nextpage = check(
                    await api.get(
                        "/v1/wardrobe/items",
                        params={"limit": 1, "cursor": page.headers["x-next-cursor"]},
                        headers=tokens[0],
                    ),
                    200,
                    "Continue wardrobe page",
                )
                assert nextpage[0]["id"] != page.json()[0]["id"]
                requestid = str(uuid.uuid4())
                outfit = check(
                    await api.post(
                        "/v1/outfits/generate",
                        headers=tokens[0],
                        json={"request_id": requestid, "context": {}},
                    ),
                    200,
                    "Generate wardrobe-backed outfit with synthetic proposer",
                )
                found = check(
                    await api.get("/v1/outfits/requests/" + requestid, headers=tokens[0]),
                    200,
                    "Recover outfit by durable request identity",
                )
                assert found["id"] == outfit["id"]
                check(
                    await api.get("/v1/outfits/requests/" + requestid, headers=tokens[1]),
                    404,
                    "Other account cannot recover outfit",
                )
                check(
                    await api.put("/v1/outfits/" + outfit["id"] + "/save", headers=tokens[0]),
                    200,
                    "Save generated outfit",
                )
                saved = check(
                    await api.get("/v1/outfits", headers=tokens[0]), 200, "Reopen saved collection"
                )
                assert saved["items"][0]["id"] == outfit["id"]
                nested = users[0] + "/legacy/nested-test.png"
                paths.append(nested)
                record()
                r = await real.post(
                    settings.supabase_url + "/storage/v1/object/wardrobe-items/" + nested,
                    headers={**admin, "Content-Type": "image/png"},
                    content=image.getvalue(),
                )
                r.raise_for_status()
                for _ in range(2):
                    check(
                        await api.request(
                            "DELETE",
                            "/v1/me/account",
                            headers=tokens[0],
                            json={"confirmation": "DELETE"},
                        ),
                        202,
                        "Account deletion accepted and retryable",
                    )
                check(
                    await api.get("/v1/wardrobe/items?limit=50", headers=tokens[0]),
                    401,
                    "Deleting account is blocked from normal APIs",
                )
                direct = await real.get(
                    settings.supabase_url + "/rest/v1/wardrobe_items",
                    headers={**public, **tokens[0]},
                    params={"select": "id"},
                )
                direct.raise_for_status()
                assert direct.json() == []
                print("Direct client wardrobe access blocked after deletion: passed", flush=True)
                r = await real.patch(
                    settings.supabase_url + "/rest/v1/account_deletions",
                    headers=admin,
                    params={"user_id": "eq." + users[0]},
                    json={"requested_at": "2000-01-01T00:00:00+00:00"},
                )
                r.raise_for_status()

                class FixtureWorker(SupabaseLifecycle):
                    async def request(self, method, path, **kwargs):
                        if path == "/rest/v1/rpc/orphaned_wardrobe_images":
                            return httpx.Response(200, json=[])
                        if path in [
                            "/rest/v1/account_deletions",
                            "/rest/v1/scan_requests",
                            "/rest/v1/outfits",
                        ]:
                            kwargs.setdefault("params", {})["user_id"] = "eq." + users[0]
                        return await super().request(method, path, **kwargs)

                stats = await FixtureWorker(real, settings).maintain()
                assert stats["accounts_completed"] == 1
                r = await real.get(
                    settings.supabase_url + "/auth/v1/admin/users/" + users[0], headers=admin
                )
                assert r.status_code == 404
                print(
                    "Maintenance deletes fixture images, owned rows and auth account: passed",
                    flush=True,
                )
                objects = await real.post(
                    settings.supabase_url + "/storage/v1/object/list/wardrobe-items",
                    headers=admin,
                    json={"prefix": users[0], "limit": 100},
                )
                objects.raise_for_status()
                assert objects.json() == []
                scanobjects = await real.post(
                    settings.supabase_url + "/storage/v1/object/list/scan-results",
                    headers=admin,
                    json={"prefix": users[0], "limit": 100},
                )
                scanobjects.raise_for_status()
                assert scanobjects.json() == []
                print(
                    "Full scan → save clothes → outfit → save → reopen and account cleanup passed "
                    "against live Supabase. Paid providers were stubbed.",
                    flush=True,
                )
            finally:
                clean = True
                for bucket, objects in [("wardrobe-items", paths), ("scan-results", scanpaths)]:
                    if objects:
                        r = await real.request(
                            "DELETE",
                            settings.supabase_url + "/storage/v1/object/" + bucket,
                            headers=admin,
                            json={"prefixes": objects},
                        )
                        clean &= r.status_code == 200
                for user in users:
                    r = await real.delete(
                        settings.supabase_url + "/auth/v1/admin/users/" + user, headers=admin
                    )
                    clean &= r.status_code in (200, 404)
                    r = await real.delete(
                        settings.supabase_url + "/rest/v1/account_deletions",
                        headers=admin,
                        params={"user_id": "eq." + user},
                    )
                    clean &= r.status_code in (200, 204)
                if clean:
                    STATE.unlink(missing_ok=True)
                    print("Disposable live fixtures removed.", flush=True)
                else:
                    raise RuntimeError("Fixture cleanup needs recovery; see " + str(STATE))
                wardrobe.analyze = original
                app.dependency_overrides.clear()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live", action="store_true", help="Create and delete disposable live accounts"
    )
    if not parser.parse_args().live:
        parser.error("Pass --live to run against the configured Styley test project")
    if STATE.exists():
        parser.error("Previous fixture cleanup must be completed first; see " + str(STATE))
    asyncio.run(main())

"""Durable bounded intake with metadata leases and private temporary result files."""

import asyncio
import hashlib
from datetime import UTC, datetime
from uuid import UUID

from app.domains.outfits.schemas import OutfitFailure
from app.domains.wardrobe.schemas import AnalysisResponse


class SupabaseScans:
    def __init__(self, client, settings):
        self.client, self.settings = client, settings
        self.headers = {"apikey": settings.supabase_secret_key}

    async def request(self, method, path, **kwargs):
        async with asyncio.timeout(20):
            r = await self.client.request(
                method,
                self.settings.supabase_url + path,
                headers=self.headers,
                timeout=20,
                **kwargs,
            )
            r.raise_for_status()
            return r

    async def claim(self, user, identity, content):
        r = await self.request(
            "POST",
            "/rest/v1/rpc/claim_scan",
            json={
                "p_user": user,
                "p_request": str(identity),
                "p_hash": hashlib.sha256(content).hexdigest(),
            },
        )
        return r.json()

    async def finish(self, user, identity, lease, result):
        # Lease-specific path prevents a stale worker overwriting a recovered result.
        path = f"{user}/{identity}-{lease}.json"
        raw = result.model_dump_json().encode()
        if len(raw) > 24_000_000:
            raise ValueError("Scan result exceeds budget")
        async with asyncio.timeout(20):
            r = await self.client.post(
                self.settings.supabase_url + "/storage/v1/object/scan-results/" + path,
                headers={**self.headers, "Content-Type": "application/json"},
                content=raw,
                timeout=20,
            )
            r.raise_for_status()
        r = await self.request(
            "PATCH",
            "/rest/v1/scan_requests",
            params={
                "user_id": "eq." + user,
                "request_id": "eq." + str(identity),
                "lease_token": "eq." + lease,
                "state": "eq.pending",
                "lease_until": "gt." + datetime.now(UTC).isoformat(),
            },
            json={"state": "ready"},
        )
        # Verify acknowledgement using an owner-scoped read, even if PATCH returns no body.
        row = await self.row(user, identity)
        if row["state"] != "ready" or row["lease_token"] != lease:
            raise OutfitFailure("generation_in_progress")
        return result

    async def fail(self, user, identity, lease):
        await self.request(
            "PATCH",
            "/rest/v1/scan_requests",
            params={
                "user_id": "eq." + user,
                "request_id": "eq." + str(identity),
                "lease_token": "eq." + lease,
                "state": "eq.pending",
            },
            json={"state": "failed"},
        )

    async def row(self, user, identity=None):
        params = {
            "user_id": "eq." + user,
            "select": "*",
            "order": "created_at.desc,request_id.desc",
            "limit": 1,
        }
        if identity:
            params["request_id"] = "eq." + str(identity)
        rows = (await self.request("GET", "/rest/v1/scan_requests", params=params)).json()
        if not rows:
            raise OutfitFailure("not_found")
        row = rows[0]
        if datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC):
            raise OutfitFailure("not_found")
        return row

    async def recover(self, user, identity=None):
        row = await self.row(user, identity)
        if row["state"] != "ready":
            raise OutfitFailure("generation_in_progress")
        # Never accept arbitrary paths from stored data.
        request = UUID(row["request_id"])
        lease = UUID(row["lease_token"])
        r = await self.request(
            "GET", f"/storage/v1/object/scan-results/{user}/{request}-{lease}.json"
        )
        if len(r.content) > 24_000_000:
            raise ValueError("Invalid scan result")
        return AnalysisResponse.model_validate_json(r.content)

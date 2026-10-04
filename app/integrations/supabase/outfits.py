"""Owner-scoped storage; RPCs own multi-row writes and concurrency."""

import asyncio
import base64
import json
from datetime import datetime
from uuid import UUID

import httpx

from app.core.config import Settings
from app.domains.outfits.schemas import (
    GENERATION_VERSION,
    GenerateOutfit,
    Outfit,
    OutfitFailure,
    OutfitPage,
    OutfitPiece,
    OutfitProposal,
)
from app.domains.wardrobe.schemas import WardrobeItem


class SupabaseOutfits:
    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        async with asyncio.timeout(10):
            response = await self.client.request(
                method,
                f"{self.settings.supabase_url}/rest/v1/{path}",
                headers={"apikey": self.settings.supabase_secret_key},
                timeout=10,
                **kwargs,
            )
            response.raise_for_status()
            return response

    async def _rpc(self, name: str, payload: dict):
        response = await self._request("POST", f"rpc/{name}", json=payload)
        return response.json() if response.content else None

    async def claim(self, user: str, request: GenerateOutfit, digest: str) -> dict:
        return await self._rpc(
            "claim_outfit",
            {
                "p_user": user,
                "p_request": str(request.request_id),
                "p_hash": digest,
                "p_context": request.context.model_dump(),
                "p_version": GENERATION_VERSION,
            },
        )

    async def candidates(self, user: str) -> list[dict]:
        # Bounded recent/favorite pool; no image bytes, image paths or signed URLs sent to AI.
        response = await self._request(
            "GET",
            "wardrobe_items",
            params={
                "user_id": f"eq.{user}",
                "select": "id,name,category,type,color,season,tags,length",
                "order": "is_favorite.desc,created_at.desc,id.desc",
                "limit": 100,
            },
        )
        rows = response.json()
        for row in rows:
            tags = row.get("tags") or []
            row["tags"] = [str(tag)[:60] for tag in tags[:5]]
        return rows

    async def complete(self, user: str, outfit_id: str, lease: str, proposal: OutfitProposal):
        return await self._rpc(
            "complete_outfit",
            {
                "p_user": user,
                "p_id": outfit_id,
                "p_lease": lease,
                "p_proposal": proposal.model_dump(mode="json"),
            },
        )

    async def fail(self, user: str, outfit_id: str, lease: str, code: str) -> None:
        await self._rpc(
            "fail_outfit",
            {
                "p_user": user,
                "p_id": outfit_id,
                "p_lease": lease,
                "p_code": code,
            },
        )

    async def save(self, user: str, outfit_id: UUID) -> Outfit:
        status = await self._rpc("save_outfit", {"p_user": user, "p_id": str(outfit_id)})
        if status != "saved":
            raise OutfitFailure(status)
        return await self.get(user, outfit_id)

    async def get(self, user: str, outfit_id: UUID | str) -> Outfit:
        rows = (
            await self._request(
                "GET",
                "outfits",
                params={
                    "user_id": f"eq.{user}",
                    "id": f"eq.{outfit_id}",
                    "select": "*,outfit_pieces(*)",
                    "limit": 1,
                },
            )
        ).json()
        if not rows:
            raise OutfitFailure("not_found")
        row = rows[0]
        if row["state"] != "ready":
            raise OutfitFailure(row.get("failure_code") or "generation_in_progress")
        return (await self._hydrate(user, rows))[0]

    async def list_saved(self, user: str, limit: int, cursor: str | None) -> OutfitPage:
        params = {
            "user_id": f"eq.{user}",
            "saved_at": "not.is.null",
            "select": "*,outfit_pieces(*)",
            "order": "saved_at.desc,id.desc",
            "limit": limit + 1,
        }
        if cursor:
            try:
                values = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
                stamp = datetime.fromisoformat(values["saved_at"])
                if stamp.tzinfo is None:
                    raise ValueError("Timezone required")
                timestamp = stamp.isoformat()
                identity = str(UUID(values["id"]))
            except (ValueError, KeyError, TypeError):
                raise OutfitFailure("invalid_cursor") from None
            params["or"] = (
                f"(saved_at.lt.{timestamp},and(saved_at.eq.{timestamp},id.lt.{identity}))"
            )
        rows = (await self._request("GET", "outfits", params=params)).json()
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = (
                base64.urlsafe_b64encode(
                    json.dumps(
                        {
                            "saved_at": last["saved_at"],
                            "id": last["id"],
                        }
                    ).encode()
                )
                .decode()
                .rstrip("=")
            )
        return OutfitPage(items=await self._hydrate(user, rows[:limit]), next_cursor=next_cursor)

    async def _hydrate(self, user: str, rows: list[dict]) -> list[Outfit]:
        ids = sorted(
            {
                p["wardrobe_item_id"]
                for row in rows
                for p in row["outfit_pieces"]
                if p["wardrobe_item_id"]
            }
        )
        garments = {}
        if ids:
            raw = (
                await self._request(
                    "GET",
                    "wardrobe_items",
                    params={
                        "user_id": f"eq.{user}",
                        "id": f"in.({','.join(ids)})",
                        "select": "*",
                    },
                )
            ).json()
            images = await self._signed_images([r["image_path"] for r in raw])
            garments = {
                r["id"]: WardrobeItem.model_validate(
                    {**r, "image_url": images.get(r["image_path"])}
                )
                for r in raw
            }

        result = []
        for row in rows:
            pieces = []
            for piece in sorted(row["outfit_pieces"], key=lambda p: p["position"]):
                item = garments.get(piece["wardrobe_item_id"])
                pieces.append(
                    OutfitPiece(
                        slot=piece["slot"],
                        garment_id=piece["garment_id"],
                        available=item is not None,
                        snapshot=piece["snapshot"],
                        item=item,
                    )
                )
            result.append(
                Outfit(
                    **{
                        k: row[k]
                        for k in (
                            "id",
                            "request_id",
                            "context",
                            "title_en",
                            "title_he",
                            "reason_en",
                            "reason_he",
                            "created_at",
                            "saved_at",
                        )
                    },
                    pieces=pieces,
                )
            )
        return result

    async def _signed_images(self, paths: list[str]) -> dict[str, str]:
        """Batch signing avoids one Storage request per garment; outages preserve metadata."""

        async def batch(part: list[str]) -> dict[str, str]:
            try:
                async with asyncio.timeout(10):
                    response = await self.client.post(
                        f"{self.settings.supabase_url}/storage/v1/object/sign/wardrobe-items",
                        headers={"apikey": self.settings.supabase_secret_key},
                        json={"paths": part, "expiresIn": 3600},
                        timeout=10,
                    )
                    response.raise_for_status()
                    values = response.json()
                    allowed = set(part)
                    return {
                        v["path"]: f"{self.settings.supabase_url}/storage/v1{v['signedURL']}"
                        for v in values
                        if isinstance(v, dict)
                        and v.get("path") in allowed
                        and isinstance(v.get("signedURL"), str)
                        and v["signedURL"].startswith("/object/sign/wardrobe-items/")
                    }
            except (httpx.HTTPError, TimeoutError, ValueError, TypeError, KeyError):
                return {}

        batches = await asyncio.gather(
            *(batch(paths[i : i + 100]) for i in range(0, len(paths), 100))
        )
        return {path: url for values in batches for path, url in values.items()}

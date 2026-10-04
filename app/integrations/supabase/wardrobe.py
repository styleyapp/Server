import base64
import io
import logging

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import Settings
from app.domains.wardrobe.schemas import CreateItem, UpdateItem, WardrobeItem


class SupabaseWardrobe:
    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings

    @property
    def _headers(self) -> dict[str, str]:
        return {"apikey": self.settings.supabase_secret_key}

    async def user_id(self, token: str) -> str | None:
        response = await self.client.get(
            f"{self.settings.supabase_url}/auth/v1/user",
            headers={
                "apikey": self.settings.supabase_publishable_key,
                "Authorization": f"Bearer {token}",
            },
            timeout=10,
        )
        if response.status_code != 200:
            return None
        return response.json().get("id")

    async def _signed_url(self, path: str) -> str | None:
        response = await self.client.post(
            f"{self.settings.supabase_url}/storage/v1/object/sign/wardrobe-items/{path}",
            headers=self._headers,
            json={"expiresIn": 86400},
            timeout=10,
        )
        response.raise_for_status()
        signed = response.json().get("signedURL")
        if not signed:
            return None
        return f"{self.settings.supabase_url}/storage/v1{signed}"

    async def _item(self, row: dict) -> WardrobeItem:
        return WardrobeItem(
            id=row["id"],
            name=row["name"],
            brand=row.get("brand") or "",
            category=row.get("category") or "",
            type=row.get("type") or "",
            color=row.get("color") or "",
            season=row.get("season") or "",
            tags=row.get("tags") or [],
            hebrew=row.get("hebrew"),
            length=row.get("length") or "",
            is_favorite=row.get("is_favorite", False),
            image_url=await self._signed_url(row["image_path"]),
            source=row["source"],
        )

    async def create(self, user_id: str, item: CreateItem) -> WardrobeItem:
        existing = await self._find_candidate(user_id, str(item.candidate_id))
        if existing:
            return await self._item(existing)
        try:
            raw = base64.b64decode(item.image_base64, validate=True)
            if len(raw) > 3 * 1024 * 1024:
                raise ValueError("Invalid image")
            image_data = Image.open(io.BytesIO(raw))
            if image_data.width * image_data.height > 25_000_000:
                raise ValueError("Invalid image")
            image_data = ImageOps.exif_transpose(image_data)
            image_data.thumbnail((1200, 1200))
            output = io.BytesIO()
            if item.image_mime == "image/png":
                image_data.convert("RGBA").save(output, format="PNG", optimize=True)
            else:
                image_data.convert("RGB").save(output, format="JPEG", quality=88)
            image = output.getvalue()
        except (ValueError, UnidentifiedImageError, OSError) as error:
            raise ValueError("Invalid image") from error
        if len(image) > 3 * 1024 * 1024:
            raise ValueError("Invalid image")
        extension = "png" if item.image_mime == "image/png" else "jpg"
        path = f"{user_id}/{item.candidate_id}.{extension}"
        storage_url = f"{self.settings.supabase_url}/storage/v1/object/wardrobe-items/{path}"
        upload = await self.client.post(
            storage_url,
            headers={**self._headers, "Content-Type": item.image_mime},
            content=image,
            timeout=20,
        )
        if upload.status_code == 409:
            existing = await self._find_candidate(user_id, str(item.candidate_id))
            if existing:
                return await self._item(existing)
        upload.raise_for_status()
        row = {
            **item.model_dump(exclude={"candidate_id", "image_base64", "image_mime"}),
            "user_id": user_id,
            "source_candidate_id": str(item.candidate_id),
            "image_path": path,
        }
        response = await self.client.post(
            f"{self.settings.supabase_url}/rest/v1/wardrobe_items",
            headers={
                **self._headers,
                "Prefer": "resolution=ignore-duplicates,return=representation",
            },
            params={"on_conflict": "user_id,source_candidate_id"},
            json=row,
            timeout=15,
        )
        if response.is_error:
            await self.client.delete(storage_url, headers=self._headers, timeout=10)
        response.raise_for_status()
        rows = response.json()
        if not rows:
            existing = await self._find_candidate(user_id, str(item.candidate_id))
            if existing:
                return await self._item(existing)
            raise ValueError("Item was not saved")
        return await self._item(rows[0])

    async def _find_candidate(self, user_id: str, candidate_id: str) -> dict | None:
        response = await self.client.get(
            f"{self.settings.supabase_url}/rest/v1/wardrobe_items",
            headers=self._headers,
            params={
                "user_id": f"eq.{user_id}",
                "source_candidate_id": f"eq.{candidate_id}",
                "select": "*",
                "limit": "1",
            },
            timeout=10,
        )
        response.raise_for_status()
        rows = response.json()
        return rows[0] if rows else None

    async def list_items(self, user_id: str) -> list[WardrobeItem]:
        response = await self.client.get(
            f"{self.settings.supabase_url}/rest/v1/wardrobe_items",
            headers=self._headers,
            params={
                "user_id": f"eq.{user_id}",
                "select": "*",
                "order": "created_at.desc",
                "limit": "100",
            },
            timeout=15,
        )
        response.raise_for_status()
        return [await self._item(row) for row in response.json()]

    async def update(self, user_id: str, item_id: str, item: UpdateItem) -> WardrobeItem | None:
        response = await self.client.patch(
            f"{self.settings.supabase_url}/rest/v1/wardrobe_items",
            headers={**self._headers, "Prefer": "return=representation"},
            params={"id": f"eq.{item_id}", "user_id": f"eq.{user_id}"},
            json=item.model_dump(exclude_unset=True),
            timeout=15,
        )
        response.raise_for_status()
        rows = response.json()
        return await self._item(rows[0]) if rows else None

    async def favorite(self, user_id: str, item_id: str, value: bool) -> WardrobeItem | None:
        response = await self.client.patch(
            f"{self.settings.supabase_url}/rest/v1/wardrobe_items",
            headers={**self._headers, "Prefer": "return=representation"},
            params={"id": f"eq.{item_id}", "user_id": f"eq.{user_id}"},
            json={"is_favorite": value},
            timeout=15,
        )
        response.raise_for_status()
        rows = response.json()
        return await self._item(rows[0]) if rows else None

    async def delete(self, user_id: str, item_id: str) -> bool:
        response = await self.client.delete(
            f"{self.settings.supabase_url}/rest/v1/wardrobe_items",
            headers={**self._headers, "Prefer": "return=representation"},
            params={"id": f"eq.{item_id}", "user_id": f"eq.{user_id}"},
            timeout=15,
        )
        response.raise_for_status()
        rows = response.json()
        if not rows:
            return False
        # The item is deleted even if private storage cleanup needs later reconciliation.
        try:
            cleanup = await self.client.request(
                "DELETE",
                f"{self.settings.supabase_url}/storage/v1/object/wardrobe-items",
                headers=self._headers,
                json={"prefixes": [rows[0]["image_path"]]},
                timeout=15,
            )
            cleanup.raise_for_status()
        except httpx.HTTPError:
            logging.getLogger(__name__).warning("Wardrobe image cleanup failed after deletion")
        return True

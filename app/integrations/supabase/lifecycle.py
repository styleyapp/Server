"""Deletion outbox and bounded, repeatable maintenance. No user payloads in logs."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx

from app.integrations.supabase.scans import SupabaseScans


class SupabaseLifecycle(SupabaseScans):
    async def authenticated_user(self, token):
        async with asyncio.timeout(10):
            r = await self.client.get(
                self.settings.supabase_url + "/auth/v1/user",
                headers={
                    "apikey": self.settings.supabase_publishable_key,
                    "Authorization": "Bearer " + token,
                },
                timeout=10,
            )
        return r.json().get("id") if r.status_code == 200 else None

    async def enqueue_deletion(self, user):
        # Prefer ignore-duplicates preserves the original grace period on retries.
        async with asyncio.timeout(15):
            r = await self.client.post(
                self.settings.supabase_url + "/rest/v1/account_deletions",
                headers={**self.headers, "Prefer": "resolution=ignore-duplicates"},
                params={"on_conflict": "user_id"},
                json={"user_id": user},
                timeout=15,
            )
            r.raise_for_status()

    async def clear_prefix(self, bucket, prefix, search=None):
        for _ in range(20):
            if search is None:
                rows = (
                    await self.request(
                        "POST",
                        "/rest/v1/rpc/account_storage_paths",
                        json={"p_user": str(UUID(prefix)), "p_bucket": bucket},
                    )
                ).json()
                paths = [row["name"] for row in rows]
                if any(
                    not isinstance(path, str) or not path.startswith(prefix + "/") for path in paths
                ):
                    raise ValueError("Unexpected storage owner")
            else:
                rows = (
                    await self.request(
                        "POST",
                        f"/storage/v1/object/list/{bucket}",
                        json={"prefix": prefix, "limit": 100, "offset": 0, "search": search},
                    )
                ).json()
                paths = []
                for row in rows:
                    name = row.get("name")
                    if not isinstance(name, str) or "/" in name or not row.get("id"):
                        raise ValueError("Unexpected storage layout")
                    paths.append(prefix + "/" + name)
            if not paths:
                return True
            await self.request("DELETE", f"/storage/v1/object/{bucket}", json={"prefixes": paths})
        return False  # Continue the same job on the next maintenance run.

    async def maintain(self):
        now = datetime.now(UTC)
        jobs = (
            await self.request(
                "GET",
                "/rest/v1/account_deletions",
                params={
                    "completed_at": "is.null",
                    "requested_at": "lt." + (now - timedelta(minutes=8)).isoformat(),
                    "select": "user_id,attempts",
                    "limit": 5,
                    "order": "last_attempt_at.asc.nullsfirst,requested_at.asc",
                },
            )
        ).json()
        completed = 0
        failed = 0
        for job in jobs:
            user = str(UUID(job["user_id"]))
            try:
                await self.request(
                    "PATCH",
                    "/rest/v1/account_deletions",
                    params={"user_id": "eq." + user},
                    json={"last_attempt_at": now.isoformat(), "attempts": job["attempts"] + 1},
                )
                if not await self.clear_prefix("wardrobe-items", user):
                    continue
                if not await self.clear_prefix("scan-results", user):
                    continue
                # Supabase Auth deletion cascades all owned database records.
                async with asyncio.timeout(15):
                    r = await self.client.delete(
                        self.settings.supabase_url + "/auth/v1/admin/users/" + user,
                        headers=self.headers,
                        timeout=15,
                    )
                    if r.status_code != 404:
                        r.raise_for_status()
                await self.request(
                    "PATCH",
                    "/rest/v1/account_deletions",
                    params={"user_id": "eq." + user},
                    json={"completed_at": now.isoformat()},
                )
                completed += 1
            except (TimeoutError, OSError, ValueError):
                failed += 1
            except httpx.HTTPError:
                failed += 1
        expired = (
            await self.request(
                "GET",
                "/rest/v1/scan_requests",
                params={
                    "expires_at": "lt." + now.isoformat(),
                    "select": "user_id,request_id",
                    "limit": 20,
                },
            )
        ).json()
        scans = 0
        for row in expired:
            user = str(UUID(row["user_id"]))
            identity = str(UUID(row["request_id"]))
            # Every lease file shares this prefix, including superseded workers.
            if await self.clear_prefix("scan-results", user, search=identity):
                await self.request(
                    "DELETE",
                    "/rest/v1/scan_requests",
                    params={
                        "user_id": "eq." + user,
                        "request_id": "eq." + identity,
                        "expires_at": "lt." + now.isoformat(),
                    },
                )
                scans += 1
        orphans = (
            await self.request("POST", "/rest/v1/rpc/orphaned_wardrobe_images", json={})
        ).json()
        if orphans:
            await self.request(
                "DELETE",
                "/storage/v1/object/wardrobe-items",
                json={"prefixes": [row["name"] for row in orphans]},
            )
        await self.request(
            "DELETE",
            "/rest/v1/outfits",
            params={
                "saved_at": "is.null",
                "created_at": "lt." + (now - timedelta(days=7)).isoformat(),
            },
        )
        await self.request(
            "DELETE",
            "/rest/v1/account_deletions",
            params={"completed_at": "lt." + (now - timedelta(days=30)).isoformat()},
        )
        return {
            "accounts_completed": completed,
            "accounts_retrying": failed,
            "scans_expired": scans,
            "orphan_images_removed": len(orphans),
        }

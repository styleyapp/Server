import httpx

from app.core.config import Settings
from app.domains.users.preferences import (
    Preferences,
    PreferencesConflict,
    PreferencesSnapshot,
    SavePreferences,
)


class SupabasePreferences:
    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self.client = client
        self.url = f"{settings.supabase_url}/rest/v1/user_preferences"
        self.headers = {"apikey": settings.supabase_secret_key}

    @staticmethod
    def _snapshot(row: dict) -> PreferencesSnapshot:
        return PreferencesSnapshot(
            preferences=Preferences.model_validate(row["preferences"]), revision=row["revision"]
        )

    async def get(self, user_id: str) -> PreferencesSnapshot:
        response = await self.client.get(
            self.url,
            headers=self.headers,
            params={"user_id": f"eq.{user_id}", "select": "preferences,revision", "limit": "1"},
            timeout=10,
        )
        response.raise_for_status()
        rows = response.json()
        return (
            self._snapshot(rows[0])
            if rows
            else PreferencesSnapshot(preferences=Preferences(), revision=0)
        )

    async def save(self, user_id: str, request: SavePreferences) -> PreferencesSnapshot:
        current = await self.get(user_id)
        # A lost HTTP response can be retried without incrementing or overwriting revisions.
        if current.revision > 0 and current.preferences == request.preferences:
            return current
        if current.revision != request.expected_revision:
            raise PreferencesConflict()
        payload = {
            "preferences": request.preferences.model_dump(),
            "revision": current.revision + 1,
        }
        if current.revision == 0:
            response = await self.client.post(
                self.url,
                headers={
                    **self.headers,
                    "Prefer": "resolution=ignore-duplicates,return=representation",
                },
                params={"on_conflict": "user_id"},
                json={"user_id": user_id, **payload},
                timeout=10,
            )
        else:
            response = await self.client.patch(
                self.url,
                headers={**self.headers, "Prefer": "return=representation"},
                params={"user_id": f"eq.{user_id}", "revision": f"eq.{current.revision}"},
                json=payload,
                timeout=10,
            )
        response.raise_for_status()
        rows = response.json()
        if rows:
            return self._snapshot(rows[0])
        # Compare-and-set lost a race. Identical requests are safe to acknowledge.
        latest = await self.get(user_id)
        if latest.revision > 0 and latest.preferences == request.preferences:
            return latest
        raise PreferencesConflict()

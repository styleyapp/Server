from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.dependencies import Owner
from app.domains.users.preferences import PreferencesConflict, PreferencesSnapshot, SavePreferences
from app.integrations.supabase.preferences import SupabasePreferences

router = APIRouter(prefix="/v1/me/preferences", tags=["preferences"])


def preferences_repository(request: Request) -> SupabasePreferences:
    return SupabasePreferences(request.app.state.http, request.app.state.settings)


PreferencesRepository = Annotated[SupabasePreferences, Depends(preferences_repository)]


@router.get("", response_model=PreferencesSnapshot)
async def get_preferences(user_id: Owner, repo: PreferencesRepository) -> PreferencesSnapshot:
    return await repo.get(user_id)


@router.put("", response_model=PreferencesSnapshot)
async def save_preferences(
    request: SavePreferences, user_id: Owner, repo: PreferencesRepository
) -> PreferencesSnapshot:
    try:
        return await repo.save(user_id, request)
    except PreferencesConflict as error:
        raise HTTPException(
            status_code=409, detail="Preferences changed. Reload before saving again."
        ) from error

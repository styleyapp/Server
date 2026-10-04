from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import Owner
from app.domains.outfits.generation import generate
from app.domains.outfits.schemas import GenerateOutfit, Outfit, OutfitFailure, OutfitPage
from app.integrations.ai.gemini import Gemini
from app.integrations.supabase.outfits import SupabaseOutfits
from app.integrations.supabase.preferences import SupabasePreferences

router = APIRouter(prefix="/v1/outfits", tags=["outfits"])


def outfit_repository(request: Request) -> SupabaseOutfits:
    return SupabaseOutfits(request.app.state.http, request.app.state.settings)


def outfit_preferences(request: Request) -> SupabasePreferences:
    return SupabasePreferences(request.app.state.http, request.app.state.settings)


def outfit_proposer(request: Request) -> Gemini:
    settings = request.app.state.settings
    return Gemini(
        request.app.state.http, settings.google_cloud_project, settings.google_cloud_location
    )


Store = Annotated[SupabaseOutfits, Depends(outfit_repository)]
Preferences = Annotated[SupabasePreferences, Depends(outfit_preferences)]
Proposer = Annotated[Gemini, Depends(outfit_proposer)]


@router.post("/generate", response_model=Outfit)
async def generate_outfit(
    body: GenerateOutfit, user: Owner, store: Store, preferences: Preferences, proposer: Proposer
) -> Outfit:
    try:
        return await generate(user, body, store, preferences, proposer)
    except (httpx.HTTPError, TimeoutError):
        raise OutfitFailure("provider_unavailable") from None


@router.get("", response_model=OutfitPage)
async def saved_outfits(
    user: Owner,
    store: Store,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> OutfitPage:
    return await store.list_saved(user, limit, cursor)


@router.get("/{outfit_id}", response_model=Outfit)
async def get_outfit(outfit_id: UUID, user: Owner, store: Store) -> Outfit:
    return await store.get(user, outfit_id)


@router.put("/{outfit_id}/save", response_model=Outfit)
async def save_outfit(outfit_id: UUID, user: Owner, store: Store) -> Outfit:
    return await store.save(user, outfit_id)

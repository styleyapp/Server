import subprocess
from typing import Annotated

import httpx
from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from google.auth.exceptions import GoogleAuthError

from app.api.dependencies import Owner, Repository, owner, repository  # noqa: F401
from app.domains.wardrobe.intake import CatalogImageError, analyze
from app.domains.wardrobe.schemas import (
    AnalysisResponse,
    CreateItem,
    FavoriteUpdate,
    UpdateItem,
    WardrobeItem,
)
from app.integrations.ai.bria import Bria
from app.integrations.ai.gemini import Gemini

router = APIRouter(prefix="/v1/wardrobe", tags=["wardrobe"])


@router.post("/analyze", response_model=AnalysisResponse)
async def analyze_media(
    request: Request,
    file: Annotated[UploadFile, File()],
    _user_id: Owner,
) -> AnalysisResponse:
    mime = file.content_type or ""
    if mime not in (
        "image/jpeg",
        "image/png",
        "image/heic",
        "image/heif",
        "video/mp4",
        "video/quicktime",
    ):
        raise HTTPException(status_code=415, detail="Choose a photo or short video")
    video = mime.startswith("video/")
    limit = 30 * 1024 * 1024 if video else 10 * 1024 * 1024
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(status_code=413, detail="File is too large")
    settings = request.app.state.settings
    client: httpx.AsyncClient = request.app.state.http
    try:
        candidates = await analyze(
            content,
            is_video=video,
            ffmpeg_binary=settings.ffmpeg_binary,
            gemini=Gemini(
                client,
                settings.google_cloud_project,
                settings.google_cloud_location,
            ),
            background_remover=Bria(client, settings.replicate_api_token),
        )
    except (ValueError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise HTTPException(status_code=422, detail="This media could not be read") from error
    except (httpx.HTTPError, GoogleAuthError, CatalogImageError) as error:
        raise HTTPException(
            status_code=502, detail="Clothing analysis is temporarily unavailable"
        ) from error
    return AnalysisResponse(candidates=candidates)


@router.get("/items", response_model=list[WardrobeItem])
async def list_items(user_id: Owner, repo: Repository) -> list[WardrobeItem]:
    return await repo.list_items(user_id)


@router.post("/items", response_model=WardrobeItem, status_code=201)
async def create_item(
    item: CreateItem,
    user_id: Owner,
    repo: Repository,
) -> WardrobeItem:
    try:
        return await repo.create(user_id, item)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid item image") from error


@router.patch("/items/{item_id}", response_model=WardrobeItem)
async def update_item(
    item_id: str,
    item: UpdateItem,
    user_id: Owner,
    repo: Repository,
) -> WardrobeItem:
    updated = await repo.update(user_id, item_id, item)
    if updated is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return updated


@router.patch("/items/{item_id}/favorite", response_model=WardrobeItem)
async def favorite_item(
    item_id: str, item: FavoriteUpdate, user_id: Owner, repo: Repository
) -> WardrobeItem:
    updated = await repo.favorite(user_id, item_id, item.is_favorite)
    if updated is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return updated


@router.delete("/items/{item_id}", status_code=204)
async def delete_item(item_id: str, user_id: Owner, repo: Repository) -> Response:
    # Idempotent: retries after a lost response are successful too.
    await repo.delete(user_id, item_id)
    return Response(status_code=204)

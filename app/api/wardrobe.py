import subprocess
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from google.auth.exceptions import GoogleAuthError

from app.domains.wardrobe.intake import analyze
from app.domains.wardrobe.schemas import AnalysisResponse, CreateItem, UpdateItem, WardrobeItem
from app.integrations.ai.bria import Bria
from app.integrations.ai.gemini import Gemini
from app.integrations.supabase.wardrobe import SupabaseWardrobe

router = APIRouter(prefix="/v1/wardrobe", tags=["wardrobe"])


def repository(request: Request) -> SupabaseWardrobe:
    return SupabaseWardrobe(request.app.state.http, request.app.state.settings)


Repository = Annotated[SupabaseWardrobe, Depends(repository)]


async def owner(
    repo: Repository,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Sign in required")
    user_id = await repo.user_id(authorization[7:])
    if not user_id:
        raise HTTPException(status_code=401, detail="Session expired")
    return user_id


Owner = Annotated[str, Depends(owner)]


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
            bria=Bria(client, settings.replicate_api_token),
            gemini=Gemini(
                client,
                settings.google_cloud_project,
                settings.google_cloud_location,
            ),
        )
    except (ValueError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise HTTPException(status_code=422, detail="This media could not be read") from error
    except (httpx.HTTPError, GoogleAuthError) as error:
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

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class GarmentLabels(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    brand: str = Field(default="", max_length=120)
    category: str = Field(default="", max_length=80)
    type: str = Field(default="", max_length=80)
    color: str = Field(default="", max_length=80)
    season: str = Field(default="", max_length=80)
    tags: list[str] = Field(default_factory=list, max_length=20)


class GarmentMetadata(GarmentLabels):
    hebrew: GarmentLabels | None = None
    length: Literal["", "short", "regular", "long"] = ""


class FavoriteUpdate(BaseModel):
    is_favorite: bool


class Candidate(GarmentMetadata):
    id: str
    image_base64: str
    image_mime: str = "image/png"
    source: str


class AnalysisResponse(BaseModel):
    candidates: list[Candidate]


class CreateItem(GarmentMetadata):
    candidate_id: UUID
    image_base64: str = Field(max_length=4_200_000)
    image_mime: Literal["image/png", "image/jpeg"] = "image/png"
    source: Literal["photo", "video"] = "photo"


class UpdateItem(GarmentMetadata):
    pass


class WardrobeItem(GarmentMetadata):
    is_favorite: bool = False
    id: str
    image_url: str | None = None
    source: Literal["photo", "video"]

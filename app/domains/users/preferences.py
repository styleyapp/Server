from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    closet_categories: list[Literal["men", "women"]] = Field(default_factory=list, max_length=2)
    # 65 represents the existing onboarding slider's "65+" choice.
    age: int | None = Field(default=None, ge=13, le=65, strict=True)
    top_fit: Literal["slim", "regular", "relaxed"] = "regular"
    pants_fit: Literal["skinny", "straight", "baggy"] = "straight"
    locale: Literal["en", "he"] = "he"

    @field_validator("closet_categories")
    @classmethod
    def canonical_categories(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("Duplicate closet categories")
        return sorted(value)


class PreferencesSnapshot(BaseModel):
    preferences: Preferences
    revision: int = Field(ge=0)


class SavePreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferences: Preferences
    expected_revision: int = Field(ge=0, strict=True)


class PreferencesConflict(Exception):
    """Another device changed this profile; never silently overwrite it."""

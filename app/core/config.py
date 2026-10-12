from dataclasses import dataclass
from os import environ
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_publishable_key: str
    supabase_secret_key: str
    google_cloud_project: str
    replicate_api_token: str = ""
    google_cloud_location: str = "global"
    ffmpeg_binary: str = "ffmpeg"
    allowed_web_origins: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
        names = (
            "SUPABASE_URL",
            "SUPABASE_PUBLISHABLE_KEY",
            "SUPABASE_SECRET_KEY",
            "GOOGLE_CLOUD_PROJECT",
        )
        missing = [name for name in names if not environ.get(name)]
        if missing:
            raise RuntimeError(f"Missing server configuration: {', '.join(missing)}")
        return cls(
            supabase_url=environ["SUPABASE_URL"].rstrip("/"),
            supabase_publishable_key=environ["SUPABASE_PUBLISHABLE_KEY"],
            supabase_secret_key=environ["SUPABASE_SECRET_KEY"],
            google_cloud_project=environ["GOOGLE_CLOUD_PROJECT"],
            replicate_api_token=environ.get("REPLICATE_API_TOKEN", ""),
            google_cloud_location=environ.get("GOOGLE_CLOUD_LOCATION", "global"),
            ffmpeg_binary=environ.get("FFMPEG_BINARY", "ffmpeg"),
            allowed_web_origins=tuple(
                v.strip() for v in environ.get("ALLOWED_WEB_ORIGINS", "").split(",") if v.strip()
            ),
        )

import asyncio
import base64
import json
from collections.abc import Callable

import google.auth
import httpx
from google.auth.credentials import Credentials
from google.auth.transport.requests import Request

_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_MODEL = "gemini-3.1-flash-image"
_DETECTION_MODEL = "gemini-2.5-flash"
_DETECTION_PROMPT = (
    "Find each distinct, clearly visible clothing item in this image. "
    "Include clothes worn by a person, on the floor, or hanging. "
    "Exclude people, body parts, shoes, bags, furniture, and clothes "
    "too obscured to identify. Return at most 8 items. Give each item's "
    "tight box_2d as [ymin, xmin, ymax, xmax] on a 0-1000 scale. "
    "Use short English values for name, category, type, color, season, "
    "and tags. Use an empty string or empty list when uncertain; never "
    "guess a brand or material. Set worn true only when the item is on a person."
)
_DETECTION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "box_2d": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                    "name": {"type": "STRING"},
                    "category": {"type": "STRING"},
                    "type": {"type": "STRING"},
                    "color": {"type": "STRING"},
                    "season": {"type": "STRING"},
                    "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "worn": {"type": "BOOLEAN"},
                },
                "required": [
                    "box_2d",
                    "name",
                    "category",
                    "type",
                    "color",
                    "season",
                    "tags",
                    "worn",
                ],
            },
        }
    },
    "required": ["items"],
}


class Gemini:
    def __init__(
        self,
        client: httpx.AsyncClient,
        project_id: str,
        location: str = "global",
        *,
        credentials_factory: Callable[[], Credentials] | None = None,
    ) -> None:
        self.client = client
        self.project_id = project_id
        self.location = location
        self._credentials_factory = credentials_factory or self._default_credentials
        self._credentials: Credentials | None = None

    @property
    def configured(self) -> bool:
        return bool(self.project_id)

    @staticmethod
    def _default_credentials() -> Credentials:
        credentials, _ = google.auth.default(scopes=[_SCOPE])
        return credentials

    def _access_token(self) -> str:
        if self._credentials is None:
            self._credentials = self._credentials_factory()
        if not self._credentials.valid:
            self._credentials.refresh(Request())
        if not self._credentials.token:
            raise RuntimeError("Google Cloud credentials did not provide an access token")
        return self._credentials.token

    @property
    def _url(self) -> str:
        return self._model_url(_MODEL)

    def _model_url(self, model: str) -> str:
        host = (
            "aiplatform.googleapis.com"
            if self.location == "global"
            else f"{self.location}-aiplatform.googleapis.com"
        )
        return (
            f"https://{host}/v1/projects/{self.project_id}/locations/{self.location}"
            f"/publishers/google/models/{model}:generateContent"
        )

    async def detect_garments(self, image: bytes) -> list[dict]:
        """Return up to eight visible garments with normalized 0-1000 boxes."""
        if not self.configured:
            raise RuntimeError("Google Cloud project is not configured")
        token = await asyncio.to_thread(self._access_token)
        response = await self.client.post(
            self._model_url(_DETECTION_MODEL),
            headers={"Authorization": f"Bearer {token}"},
            json={
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"text": _DETECTION_PROMPT},
                            {
                                "inlineData": {
                                    "mimeType": "image/jpeg",
                                    "data": base64.b64encode(image).decode("ascii"),
                                }
                            },
                        ],
                    }
                ],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseSchema": _DETECTION_SCHEMA,
                    "thinkingConfig": {"thinkingBudget": 0},
                    "maxOutputTokens": 1200,
                },
            },
            timeout=45,
        )
        response.raise_for_status()
        candidates = response.json().get("candidates", [])
        if not candidates:
            raise httpx.ProtocolError("Vertex AI returned no garment analysis")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = next((part.get("text") for part in parts if part.get("text")), None)
        if not isinstance(text, str):
            raise httpx.ProtocolError("Vertex AI returned no garment analysis")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            raise httpx.ProtocolError("Vertex AI returned invalid garment JSON") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise httpx.ProtocolError("Vertex AI returned invalid garment analysis")
        return payload["items"][:8]

    async def isolate_worn_garment(self, image: bytes, garment_name: str) -> bytes:
        if not self.configured:
            raise RuntimeError("Google Cloud project is not configured")
        prompt = (
            "Create a clean catalog cutout of only the "
            + garment_name
            + " shown in this photo, front facing on a plain white background. "
            "Preserve its exact color, pattern, logos, cut and details. "
            "Exclude the person, other clothes, hands and background. "
            "Do not invent unseen details."
        )
        token = await asyncio.to_thread(self._access_token)
        response = await self.client.post(
            self._url,
            headers={"Authorization": f"Bearer {token}"},
            json={
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"text": prompt},
                            {
                                "inlineData": {
                                    "mimeType": "image/jpeg",
                                    "data": base64.b64encode(image).decode("ascii"),
                                }
                            },
                        ],
                    }
                ],
                "generationConfig": {
                    "responseModalities": ["TEXT", "IMAGE"],
                    "imageConfig": {
                        "imageSize": "1K",
                        "imageOutputOptions": {"mimeType": "image/png"},
                    },
                },
            },
            timeout=60,
        )
        response.raise_for_status()
        for candidate in response.json().get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                image_data = part.get("inlineData")
                if image_data and image_data.get("data"):
                    return base64.b64decode(image_data["data"], validate=True)
        raise ValueError("Vertex AI returned no image")

import json
import logging
import time
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.account import router as account_router
from app.api.outfits import router as outfits_router
from app.api.preferences import router as preferences_router
from app.api.wardrobe import router as wardrobe_router
from app.core.config import Settings
from app.core.request_limits import ConfiguredCORS, RequestLimits
from app.domains.outfits.schemas import OutfitFailure


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.settings = Settings.from_env()
    async with httpx.AsyncClient() as client:
        app.state.http = client
        yield


request_logger = logging.getLogger("styley.requests")
request_logger.setLevel(logging.INFO)
if not request_logger.handlers:
    request_logger.addHandler(logging.StreamHandler())
request_logger.propagate = False

app = FastAPI(title="Styley Server", lifespan=lifespan)
app.add_middleware(RequestLimits)
app.add_middleware(ConfiguredCORS)
app.include_router(wardrobe_router)
app.include_router(preferences_router)
app.include_router(outfits_router)
app.include_router(account_router)


@app.middleware("http")
async def trace_request(request: Request, call_next):
    request.state.trace_id = str(uuid4())
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        response = _error(request, "server_error", "Please try again later", 500)
    route = request.scope.get("route")
    request_logger.info(
        json.dumps(
            {
                "trace_id": request.state.trace_id,
                "route": getattr(route, "path", "unmatched"),
                "method": request.method,
                "status": response.status_code,
                "duration_ms": round((time.monotonic() - started) * 1000),
            }
        )
    )
    response.headers["X-Trace-ID"] = request.state.trace_id
    return response


def _error(request: Request, code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "trace_id": request.state.trace_id}},
    )


@app.exception_handler(HTTPException)
async def http_error(request: Request, error: HTTPException) -> JSONResponse:
    codes = {
        401: "unauthorized",
        404: "not_found",
        409: "conflict",
        413: "file_too_large",
        415: "unsupported_media",
        422: "invalid_input",
        502: "provider_unavailable",
    }
    return _error(
        request,
        codes.get(error.status_code, "request_failed"),
        str(error.detail),
        error.status_code,
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, _error_value: RequestValidationError) -> JSONResponse:
    return _error(request, "invalid_input", "Check the request fields", 422)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, _error_value: Exception) -> JSONResponse:
    return _error(request, "server_error", "Please try again later", 500)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.exception_handler(OutfitFailure)
async def outfit_error(request: Request, error: OutfitFailure) -> JSONResponse:
    messages = {
        "expired": (410, "This scan has expired; choose your media again"),
        "unauthorized": (401, "Sign in required"),
        "conflict": (409, "This request was already used for different choices"),
        "generation_in_progress": (409, "An outfit is still being prepared; try again shortly"),
        "rate_limited": (429, "Please wait before creating another outfit"),
        "insufficient_wardrobe": (422, "Add a top and bottom, or a one-piece garment"),
        "wardrobe_changed": (409, "Some garments are no longer available; create another outfit"),
        "not_found": (404, "Outfit not found"),
        "invalid_cursor": (422, "Restart the saved outfit list"),
        "invalid_proposal": (502, "An outfit could not be prepared; please try again"),
        "provider_unavailable": (502, "Outfit generation is temporarily unavailable"),
    }
    status, message = messages.get(
        error.code, (502, "Outfit generation is temporarily unavailable")
    )
    return _error(
        request, error.code if error.code in messages else "provider_unavailable", message, status
    )

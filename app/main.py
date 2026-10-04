from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.preferences import router as preferences_router
from app.api.wardrobe import router as wardrobe_router
from app.core.config import Settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.settings = Settings.from_env()
    async with httpx.AsyncClient() as client:
        app.state.http = client
        yield


app = FastAPI(title="Styley Server", lifespan=lifespan)
app.include_router(wardrobe_router)
app.include_router(preferences_router)


@app.middleware("http")
async def trace_request(request: Request, call_next):
    request.state.trace_id = str(uuid4())
    response = await call_next(request)
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

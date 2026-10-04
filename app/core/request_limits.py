"""Bound bodies before JSON/multipart parsing; never include payloads in errors."""

from fastapi import HTTPException
from starlette.responses import JSONResponse


class RequestLimits:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = 32 * 1024 * 1024 if scope["path"] == "/v1/wardrobe/analyze" else 6 * 1024 * 1024
        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = limit + 1
        if declared > limit:
            response = JSONResponse(
                {
                    "error": {
                        "code": "file_too_large",
                        "message": "File is too large",
                        "trace_id": scope.get("state", {}).get("trace_id"),
                    }
                },
                status_code=413,
            )
            return await response(scope, receive, send)
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > limit:
                raise HTTPException(413, detail="File is too large")
            return message

        await self.app(scope, bounded_receive, send)


class ConfiguredCORS:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        from starlette.middleware.cors import CORSMiddleware

        settings = getattr(scope.get("app").state, "settings", None) if scope.get("app") else None
        cors = CORSMiddleware(
            self.app,
            allow_origins=list(getattr(settings, "allowed_web_origins", ())),
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
            expose_headers=["X-Trace-ID", "X-Next-Cursor"],
        )
        await cors(scope, receive, send)

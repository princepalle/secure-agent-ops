"""Application errors and safe HTTP error responses.

Clients get a stable ``{"error": {"code", "message", "request_id"}}`` body.
Stack traces, SQL and file paths are logged server-side, never returned.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.headers = headers


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class PayloadTooLarge(AppError):
    status_code = 413
    code = "payload_too_large"


class UnsupportedMediaType(AppError):
    status_code = 415
    code = "unsupported_media_type"


class Unprocessable(AppError):
    status_code = 422
    code = "unprocessable"


class RateLimited(AppError):
    status_code = 429
    code = "rate_limited"


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _body(code: str, message: str, request: Request, **extra: object) -> dict[str, object]:
    return {"error": {"code": code, "message": message, "request_id": _request_id(request), **extra}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            _body(exc.code, exc.message, request), status_code=exc.status_code, headers=exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Report which fields failed and why, but never echo submitted values (could be secrets).
        details = [
            {"field": ".".join(str(p) for p in err.get("loc", ()) if p != "body"), "issue": err.get("msg")}
            for err in exc.errors()
        ]
        return JSONResponse(
            _body("validation_error", "Request validation failed", request, details=details), status_code=422
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}.get(
            exc.status_code, "http_error"
        )
        message = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return JSONResponse(
            _body(code, message, request), status_code=exc.status_code, headers=getattr(exc, "headers", None)
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(_body("internal_error", "An internal error occurred", request), status_code=500)

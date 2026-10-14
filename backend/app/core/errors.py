"""HTTP error handling: every error leaves the API as RFC 7807 ``application/problem+json``.

Shape::

    {"type": "https://httpstatuses.io/409", "title": "Conflict", "status": 409,
     "code": "BID_TOO_LOW", "detail": "...", "request_id": "...", ...extra details}

Clients switch on ``code`` (stable), never on ``detail`` (human text).
"""

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.domain.errors import DomainError

STATUS_BY_CODE: dict[str, int] = {
    "UNAUTHENTICATED": 401,
    "INVALID_CREDENTIALS": 401,
    "INVALID_TOKEN": 401,
    "FORBIDDEN": 403,
    "ADMIN_CANNOT_BID": 403,
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "EMAIL_TAKEN": 409,
    "AUCTION_NOT_LIVE": 409,
    "AUCTION_ENDED": 409,
    "BID_TOO_LOW": 409,
    "ALREADY_HIGHEST": 409,
    "IDEMPOTENCY_CONFLICT": 409,
    "INVALID_TRANSITION": 409,
    "AUCTION_NOT_EDITABLE": 409,
    "BIKE_ALREADY_LISTED": 409,
    "BID_TOO_HIGH": 422,
    "INVALID_SCHEDULE": 422,
    "VALIDATION_ERROR": 422,
    "RATE_LIMITED": 429,
    "PAYLOAD_TOO_LARGE": 413,
}

_TITLES = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    413: "Payload Too Large",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
}


def problem(
    status: int,
    code: str,
    detail: str,
    request: Request | None = None,
    headers: dict[str, str] | None = None,
    /,
    **extra: Any,
) -> JSONResponse:
    # Extension members first, standard members last: a detail can never override them.
    body: dict[str, Any] = {
        **extra,
        "type": f"https://httpstatuses.io/{status}",
        "title": _TITLES.get(status, "Error"),
        "status": status,
        "code": code,
        "detail": detail,
    }
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    if request_id:
        body["request_id"] = request_id
    return JSONResponse(
        body, status_code=status, media_type="application/problem+json", headers=headers
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError) -> JSONResponse:
        status = STATUS_BY_CODE.get(exc.code, 400)
        headers = None
        if exc.code == "RATE_LIMITED" and "retry_after" in exc.details:
            headers = {"Retry-After": str(exc.details["retry_after"])}
        if exc.code in ("UNAUTHENTICATED", "INVALID_TOKEN"):
            headers = {"WWW-Authenticate": "Bearer"}
        return problem(status, exc.code, exc.message, request, headers, **exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]}
            for e in exc.errors()
        ]
        return problem(422, "VALIDATION_ERROR", "Request validation failed", request, errors=errors)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return problem(exc.status_code, code, str(exc.detail), request, dict(exc.headers or {}))

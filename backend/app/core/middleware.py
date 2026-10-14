"""Pure-ASGI middleware (no BaseHTTPMiddleware: it breaks contextvars/streaming and costs more).

* RequestContextMiddleware: request id, structured access log, Prometheus HTTP metrics.
* SecurityHeadersMiddleware: baseline hardening headers on every response.
"""

import time
import uuid

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core import metrics
from app.core.errors import problem

log = structlog.stdlib.get_logger("app.access")

_SKIP_ACCESS_LOG = {"/healthz", "/readyz", "/metrics"}


def _route_template(scope: Scope) -> str:
    """Full path template for metrics labels, e.g. ``/api/v1/auctions/{auction_id}``.

    The matched route's own ``path`` is relative to its router prefix, so the template is rebuilt
    from the real path by swapping each path-parameter *segment* for its ``{name}``. Requests
    that matched no route share one label, which keeps label cardinality bounded.
    """
    if scope.get("route") is None:
        return "unmatched"
    values = {str(v): k for k, v in scope.get("path_params", {}).items()}
    segments = scope["path"].split("/")
    return "/".join(f"{{{values[seg]}}}" if seg in values else seg for seg in segments)


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        incoming = headers.get("x-request-id", "")
        # Accept a caller-supplied id only if it is short and harmless; otherwise mint our own.
        request_id = (
            incoming if 0 < len(incoming) <= 64 and incoming.isprintable() else uuid.uuid4().hex
        )
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        status_code = 500
        response_started = False
        started = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            try:
                await self.app(scope, receive, send_wrapper)
            except Exception:
                # Handled here rather than by Starlette's outermost handler, which runs after our
                # `finally` has cleared the context: this way the 500 still carries the request id.
                log.exception("unhandled_exception", path=scope["path"])
                if response_started:
                    raise
                error = problem(500, "INTERNAL_ERROR", "An unexpected error occurred")
                await error(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - started
            route = _route_template(scope)
            method = scope["method"]
            if route != "/metrics":
                metrics.HTTP_REQUESTS.labels(method, route, str(status_code)).inc()
                metrics.HTTP_LATENCY.labels(method, route).observe(elapsed)
            if scope["path"] not in _SKIP_ACCESS_LOG:
                log.info(
                    "http_request",
                    method=method,
                    path=scope["path"],
                    route=route,
                    status=status_code,
                    duration_ms=round(elapsed * 1000, 2),
                )
            structlog.contextvars.clear_contextvars()


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, hsts: bool = False) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                h = MutableHeaders(scope=message)
                h.setdefault("X-Content-Type-Options", "nosniff")
                h.setdefault("X-Frame-Options", "DENY")
                h.setdefault("Referrer-Policy", "no-referrer")
                h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
                h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
                if self.hsts:
                    h.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
            await send(message)

        await self.app(scope, receive, send_wrapper)


class _BodyTooLarge(BaseException):
    """Signal raised from ``receive()`` once a streamed body exceeds the limit.

    Deliberately a ``BaseException``: FastAPI wraps body reading in ``except Exception`` and would
    turn an ordinary exception into a generic 400, hiding the real reason from the client. This one
    passes through those handlers and is converted to a 413 by ``BodySizeLimitMiddleware``.
    """


class BodySizeLimitMiddleware:
    """Reject oversized request bodies before they are parsed (413).

    Checks the declared ``Content-Length`` up front and also counts bytes as they stream in, so a
    chunked upload with no length header can't slip past. Every legitimate body in this API is a
    few hundred bytes of JSON; anything near the limit is abuse.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = Headers(scope=scope).get("content-length", "")
        if declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(scope, receive, send)
            return

        received = 0
        started = False

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _BodyTooLarge:
            if not started:
                await self._reject(scope, receive, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = problem(
            413,
            "PAYLOAD_TOO_LARGE",
            f"Request body exceeds {self.max_bytes} bytes",
            max_bytes=self.max_bytes,
        )
        await response(scope, receive, send)

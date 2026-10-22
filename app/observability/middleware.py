"""Request id, security headers and HTTP metrics."""

from __future__ import annotations

import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.observability import metrics

_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}
# The interactive docs page needs scripts and styles from a CDN.
_DOCS_PATHS = ("/docs", "/redoc")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        request.state.request_id = request_id

        started = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - started

        route = request.scope.get("route")
        route_label = getattr(route, "path", "unmatched")  # template, e.g. /approvals/{approval_id}
        metrics.HTTP_REQUESTS.labels(request.method, route_label, str(response.status_code)).inc()
        metrics.HTTP_LATENCY.labels(request.method, route_label).observe(elapsed)

        response.headers["X-Request-ID"] = request_id
        for header, value in SECURITY_HEADERS.items():
            if header == "Content-Security-Policy" and request.url.path.startswith(_DOCS_PATHS):
                continue
            response.headers.setdefault(header, value)
        return response

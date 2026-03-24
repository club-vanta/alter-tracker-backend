"""
Request context middleware for tracing.

Adds a unique request_id to every request that:
  - Appears in all logs during the request
  - Is returned in the X-Request-ID response header
  - Can be passed in via X-Request-ID header (for distributed tracing)

This enables correlating logs across a single request when multiple
requests are being processed concurrently.
"""

import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

REQUEST_ID_HEADER = "X-Request-ID"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """
    Middleware that binds request context to structlog for the duration of each request.

    Context includes:
      - request_id: Unique identifier for this request
      - method: HTTP method (GET, POST, etc.)
      - path: Request path
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # Use provided request ID or generate a new one
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())[:8]

        # Clear any previous context and bind new request context
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        # Store request_id on request state for access in route handlers
        request.state.request_id = request_id

        # Process the request
        response = await call_next(request)

        # Log server errors with structured context for easy CloudWatch filtering
        if response.status_code >= 500:
            log = structlog.get_logger()
            log.error("Server error", status_code=response.status_code)

        # Add request ID to response headers for client-side correlation
        response.headers[REQUEST_ID_HEADER] = request_id

        return response

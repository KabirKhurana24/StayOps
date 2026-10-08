"""Request ID + access logging middleware (pure ASGI, streaming-safe)."""

from __future__ import annotations

import re
import time
import uuid
from contextvars import ContextVar

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import get_logger

REQUEST_ID_HEADER = "x-request-id"
_VALID_INBOUND_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
logger = get_logger("stayops.request")


def current_request_id() -> str | None:
    return request_id_var.get()


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound = None
        for key, value in scope.get("headers", []):
            if key == REQUEST_ID_HEADER.encode():
                inbound = value.decode("latin-1")
                break
        # Accept a well-formed upstream ID (e.g. from a load balancer); otherwise mint one.
        request_id = inbound if inbound and _VALID_INBOUND_ID.match(inbound) else uuid.uuid4().hex

        token = request_id_var.set(request_id)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.encode(), request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            # Path only — never the query string or headers.
            log = logger.warning if status_code >= 500 else logger.info
            log(
                "http.request",
                method=scope.get("method"),
                path=scope.get("path"),
                status=status_code,
                duration_ms=duration_ms,
            )
            structlog.contextvars.clear_contextvars()
            request_id_var.reset(token)

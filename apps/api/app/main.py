"""StayOps API entrypoint (modular monolith)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.audit.router import router as audit_router
from app.auth.router import router as auth_router
from app.calendar.router import router as calendar_router
from app.cleaning.router import router as cleaning_router
from app.command_center.router import router as command_center_router
from app.config.settings import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging, get_logger
from app.core.request_context import RequestContextMiddleware
from app.events import handlers as _event_handlers  # noqa: F401  (registers subscribers)
from app.events.router import router as events_router
from app.guest_portal.router import guest_router as guest_portal_router
from app.guest_portal.router import links_router as portal_links_router
from app.guest_requests.router import router as guest_requests_router
from app.guests.router import router as guests_router
from app.health.router import router as health_router
from app.inbox.router import router as inbox_router
from app.knowledge.router import router as knowledge_router
from app.maintenance.router import router as maintenance_router
from app.operations.router import router as operations_router
from app.organizations.router import router as organizations_router
from app.properties.router import router as properties_router
from app.reservations.router import router as reservations_router
from app.tasks.router import router as tasks_router

logger = get_logger("stayops.api")

_SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
]


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {k.lower() for k, _ in headers}
                headers.extend(h for h in _SECURITY_HEADERS if h[0] not in existing)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.use_json_logs)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Development: process the outbox on a background thread, woken after each
        # commit. Production: run `python -m app.events.worker` separately.
        worker = None
        if settings.run_embedded_worker:
            from app.events.worker import EmbeddedWorker

            worker = EmbeddedWorker(settings)
            worker.start()
        try:
            yield
        finally:
            if worker is not None:
                worker.stop()

    app = FastAPI(
        lifespan=lifespan,
        title=settings.app_name,
        version=settings.app_version,
        docs_url="/docs" if settings.app_env != "production" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.app_env != "production" else None,
    )

    # Order matters: the outermost middleware is added last.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api_cors_origins,
        allow_credentials=False,  # bearer tokens, not cookies, authenticate API calls
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Organization-Id",
            "X-Request-Id",
            "X-Guest-Token",
        ],
        expose_headers=["X-Request-Id"],
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)

    app.include_router(health_router)
    v1 = APIRouter(prefix="/api/v1")
    for router in (
        auth_router,
        organizations_router,
        properties_router,
        guests_router,
        reservations_router,
        calendar_router,
        tasks_router,
        cleaning_router,
        maintenance_router,
        operations_router,
        command_center_router,
        events_router,
        audit_router,
        inbox_router,
        guest_requests_router,
        knowledge_router,
        portal_links_router,
        guest_portal_router,
    ):
        v1.include_router(router)
    app.include_router(v1)

    if not (settings.supabase_url or settings.supabase_jwt_secret):
        logger.warning(
            "auth.not_configured",
            hint="Set SUPABASE_URL (and optionally SUPABASE_JWT_SECRET); "
            "authenticated endpoints will return 401.",
        )
    logger.info("api.started", environment=settings.app_env, cors=settings.api_cors_origins)
    return app


app = create_app()

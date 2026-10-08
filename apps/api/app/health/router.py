from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from app.config.settings import Settings, get_settings
from app.core.logging import get_logger
from app.db.session import get_engine

router = APIRouter(tags=["health"])
logger = get_logger("stayops.health")


class HealthOut(BaseModel):
    status: Literal["ok"]
    service: str
    version: str
    environment: str


class ReadinessOut(BaseModel):
    status: Literal["ok", "degraded"]
    checks: dict[str, str]


@router.get("/health", response_model=HealthOut, summary="Liveness")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthOut:
    return HealthOut(
        status="ok",
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
    )


@router.get("/health/ready", response_model=ReadinessOut, summary="Readiness (dependencies)")
def ready(settings: Annotated[Settings, Depends(get_settings)]) -> JSONResponse:
    checks: dict[str, str] = {}
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        logger.warning("health.database_unavailable", error_type=type(exc).__name__)
        checks["database"] = "unavailable"
    checks["auth"] = (
        "ok" if settings.supabase_url or settings.supabase_jwt_secret else "not_configured"
    )
    checks["redis"] = "configured" if settings.redis_url else "not_configured"
    healthy = checks["database"] == "ok"
    body = ReadinessOut(status="ok" if healthy else "degraded", checks=checks)
    return JSONResponse(status_code=200 if healthy else 503, content=body.model_dump())

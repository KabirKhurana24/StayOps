"""Domain errors and the single, consistent error envelope.

Every error response has the shape::

    {"error": {"code": "...", "message": "...", "request_id": "...", "details": ...}}

Messages are safe for clients: internal exception text, SQL and stack traces are
logged server-side only.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger
from app.core.request_context import current_request_id

logger = get_logger("stayops.errors")


class AppError(Exception):
    status_code = 400
    code = "bad_request"
    message = "The request could not be processed."

    def __init__(self, message: str | None = None, *, details: Any = None) -> None:
        self.message = message or self.message
        self.details = details
        super().__init__(self.message)


class AuthenticationError(AppError):
    status_code = 401
    code = "unauthenticated"
    message = "Authentication is required."


class PermissionDeniedError(AppError):
    status_code = 403
    code = "forbidden"
    message = "You do not have permission to perform this action."


class NoOrganizationError(AppError):
    status_code = 403
    code = "no_organization"
    message = "You are not a member of any organization yet."


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    message = "The requested resource was not found."


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    message = "The request conflicts with the current state of the resource."


class DomainValidationError(AppError):
    status_code = 422
    code = "validation_error"
    message = "The request is invalid."


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"
    message = "A required service is temporarily unavailable."


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message, "request_id": current_request_id()}
    if details is not None:
        body["details"] = details
    return {"error": body}


def _response(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse(status_code=status, content=error_body(code, message, details))


# Postgres SQLSTATE → client-safe mapping for constraint violations that slip past
# service-level checks (e.g. races). Constraint names are ours, never user data.
_PG_UNIQUE = "23505"
_PG_FOREIGN_KEY = "23503"
_PG_CHECK = "23514"
_PG_NOT_NULL = "23502"
_PG_EXCLUSION = "23P01"


def integrity_error_to_app_error(exc: IntegrityError) -> AppError:
    orig = exc.orig
    sqlstate = getattr(orig, "sqlstate", None)
    diag = getattr(orig, "diag", None)
    constraint = getattr(diag, "constraint_name", None)
    details = {"constraint": constraint} if constraint else None
    if sqlstate == _PG_EXCLUSION:
        return ConflictError(
            "The unit is already booked for an overlapping period.", details=details
        )
    if sqlstate == _PG_UNIQUE:
        return ConflictError("A record with the same unique value already exists.", details=details)
    if sqlstate == _PG_FOREIGN_KEY:
        return DomainValidationError(
            "A referenced record does not exist in this organization.", details=details
        )
    if sqlstate in (_PG_CHECK, _PG_NOT_NULL):
        return DomainValidationError("The record violates a data constraint.", details=details)
    return ConflictError(details=details)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error("app_error", code=exc.code, message=exc.message)
        return _response(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {
                "loc": [str(part) for part in err.get("loc", ())],
                "message": err.get("msg"),
                "type": err.get("type"),
            }
            for err in exc.errors()
        ]
        return _response(422, "validation_error", "The request is invalid.", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return _response(exc.status_code, code, message)

    @app.exception_handler(IntegrityError)
    async def _integrity_error(_: Request, exc: IntegrityError) -> JSONResponse:
        app_error = integrity_error_to_app_error(exc)
        logger.info("integrity_error", code=app_error.code, details=app_error.details)
        return _response(
            app_error.status_code, app_error.code, app_error.message, app_error.details
        )

    @app.exception_handler(OperationalError)
    async def _db_unavailable(_: Request, exc: OperationalError) -> JSONResponse:
        logger.error("database_unavailable", error_type=type(exc.orig).__name__)
        return _response(503, "database_unavailable", "The database is temporarily unavailable.")

    @app.exception_handler(DBAPIError)
    async def _db_error(_: Request, exc: DBAPIError) -> JSONResponse:
        logger.error("database_error", error_type=type(exc.orig).__name__, exc_info=exc)
        return _response(500, "database_error", "A database error occurred.")

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled_exception", exc_info=exc)
        return _response(500, "internal_error", "An unexpected error occurred.")

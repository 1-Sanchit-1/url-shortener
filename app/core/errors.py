from typing import cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Base class for errors that map onto a specific HTTP response."""

    status_code = 500
    code = "internal_error"

    headers: dict[str, str] | None = None

    def __init__(self, message: str = "") -> None:
        super().__init__(message)
        self.message = message or self.code.replace("_", " ")


class InvalidRequestError(AppError):
    status_code = 422
    code = "invalid_request"


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"
    headers = {"WWW-Authenticate": "Bearer"}  # noqa: RUF012


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class GoneError(AppError):
    status_code = 410
    code = "gone"


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"


async def _handle_app_error(_: Request, exc: Exception) -> JSONResponse:
    exc = cast(AppError, exc)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
        headers=exc.headers,
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)

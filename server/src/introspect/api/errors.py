"""Problem-details error responses: every API failure is JSON shaped {status, title, detail}.

Registered on the app by :func:`register_error_handlers` (called once, from
:func:`introspect.api.create_app`):

- ``LookupError`` (and subclasses route handlers raise to mean "not found") -> 404
- ``RequestValidationError`` (FastAPI's request-parsing failure -- it does NOT raise
  ``ValueError``, so it needs its own handler, not just a ``ValueError`` catch-all) -> 422,
  restyled into problem JSON instead of FastAPI's default ``{"detail": [...]}`` body
- ``StarletteHTTPException`` (raised explicitly by route code, or by Starlette itself for
  e.g. an unmatched route) -> restyled into problem JSON at its own status code
- anything else -> 500, with ``detail`` set to the exception's CLASS NAME ONLY. Never
  ``str(exc)`` or its args: those can carry internals (file paths, query text, stack
  context) that must not leak to an API client.
"""

from __future__ import annotations

from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from introspect.api.models import Problem


def _title_for(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "Error"


def _problem_response(status_code: int, detail: str) -> JSONResponse:
    problem = Problem(status=status_code, title=_title_for(status_code), detail=detail)
    return JSONResponse(status_code=status_code, content=problem.model_dump())


async def _handle_lookup_error(request: Request, exc: LookupError) -> JSONResponse:
    return _problem_response(404, str(exc) or type(exc).__name__)


async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return _problem_response(422, str(exc.errors()))


async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return _problem_response(exc.status_code, detail)


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    # NOTE(claude): detail is the class name ONLY -- never str(exc)/exc.args, which can
    # carry internals (paths, query fragments, stack context) that must not reach a client.
    return _problem_response(500, type(exc).__name__)


def register_error_handlers(app: FastAPI) -> None:
    """Wire every problem-details handler onto ``app``. Called once, from create_app."""
    app.add_exception_handler(LookupError, _handle_lookup_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(Exception, _handle_unexpected)

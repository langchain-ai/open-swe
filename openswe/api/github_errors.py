"""HTTP answers for GitHub failures raised below the routes."""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from openswe.github.http import (
    GitHubAppUnavailable,
    GitHubError,
    GitHubSignInRequired,
    GraphQLError,
)

logger = logging.getLogger(__name__)


async def _sign_in_required(_request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": "GitHub token unavailable, re-login required"}, status_code=401)


async def _app_unavailable(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=503)


async def _github_refused(_request: Request, exc: Exception) -> JSONResponse:
    """GitHub's 4xx passes through (403 = permissions, 422 = rejected input); an outage is a 502."""
    if not isinstance(exc, GitHubError):
        raise exc
    status = exc.response.status_code
    logger.warning(
        "GitHub refused a request",
        extra={
            "github_url": str(exc.request.url),
            "github_status": status,
            "github_message": exc.message,
        },
    )
    return JSONResponse({"detail": exc.message}, status_code=status if status < 500 else 502)


async def _graphql_rejected(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, GraphQLError):
        raise exc
    logger.warning("GitHub rejected a GraphQL request", extra={"github_message": exc.message})
    return JSONResponse({"detail": exc.message}, status_code=422)


def add_github_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(GitHubSignInRequired, _sign_in_required)
    app.add_exception_handler(GitHubAppUnavailable, _app_unavailable)
    app.add_exception_handler(GitHubError, _github_refused)
    app.add_exception_handler(GraphQLError, _graphql_rejected)

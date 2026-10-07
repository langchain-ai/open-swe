"""HTTP answers for GitHub authorization failures raised below the routes."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agent.github.http import GitHubAppUnavailable, GitHubSignInRequired


async def _sign_in_required(_request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": "GitHub token unavailable, re-login required"}, status_code=401)


async def _app_unavailable(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=503)


def add_github_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(GitHubSignInRequired, _sign_in_required)
    app.add_exception_handler(GitHubAppUnavailable, _app_unavailable)

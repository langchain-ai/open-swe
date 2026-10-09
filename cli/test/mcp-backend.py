"""Local HTTP backend for the CLI MCP stdio integration test."""

import os

import uvicorn
from fastapi import FastAPI, HTTPException, Request

from openswe.mcp.cli_tools import router
from openswe.tools.manage_feature_flags import manage_feature_flags
from openswe.web.oauth import require_session

app = FastAPI()
app.include_router(router, prefix="/api")


async def settings() -> dict[str, bool]:
    return {"review_draft_prs": False}


manage_feature_flags.__globals__["get_instance_settings"] = settings


def session(request: Request) -> dict[str, str]:
    user = request.cookies.get("osw_session")
    if user not in {"admin", "user"}:
        raise HTTPException(401, "invalid session")
    return {"sub": user}


app.dependency_overrides[require_session] = session

if __name__ == "__main__":
    os.environ["CONFIGURED_ADMINS"] = "admin"
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["MCP_TEST_PORT"]), log_level="error")

"""Owner-only, write-only configuration of a thread's current sandbox."""

import logging
from collections.abc import Callable, Coroutine
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from langsmith.sandbox import RunConfig
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import text

from openswe.audit_logs.middleware import audit_endpoint
from openswe.config import ENV
from openswe.dashboard.deps import SESSION_DEP
from openswe.database.postgres import engine
from openswe.sandboxes.providers.langsmith import get_async_sandbox_client
from openswe.threads.summary import thread_is_owner, thread_metadata
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)


class SecretRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(422, "Invalid environment configuration") from None

        return handle


router = APIRouter(tags=["threads"], route_class=SecretRoute)
_RESERVED_PREFIXES = (
    "OPEN_SWE",
    "OPENSWE",
    "LANGSMITH",
    "LANGCHAIN",
    "GITHUB",
    "GH_",
    "GIT_",
    "AWS_",
    "LD_",
    "PYTHON",
    "NODE_",
)
_RESERVED_NAMES = {
    "PATH",
    "HOME",
    "USER",
    "SHELL",
    "BASH_ENV",
    "ENV",
    "IFS",
    "PWD",
    "TMPDIR",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
}


class SandboxEnvironmentBody(BaseModel):
    name: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")]
    value: SecretStr
    acknowledge_shared_access: bool

    def validate_change(self) -> None:
        name = self.name.upper()
        if name in _RESERVED_NAMES or name.startswith(_RESERVED_PREFIXES):
            raise HTTPException(400, "This environment variable is reserved for the platform")
        value = self.value.get_secret_value()
        if "\0" in value or len(value.encode()) > 16384:
            raise HTTPException(400, "Value must be at most 16 KiB and contain no NUL characters")
        if not self.acknowledge_shared_access:
            raise HTTPException(400, "Acknowledge access by all threads sharing this sandbox")


@router.post("/threads/{thread_id}/sandbox/environment", status_code=204)
@audit_endpoint
async def configure_sandbox_environment(
    thread_id: str,
    body: SandboxEnvironmentBody,
    response: Response,
    session: dict[str, object] = SESSION_DEP,
) -> None:
    response.headers["Cache-Control"] = "no-store"
    body.validate_change()
    client = langgraph_client()
    thread = await client.threads.get(thread_id)
    metadata = thread_metadata(thread)
    login = session.get("sub")
    if not isinstance(login, str) or not thread_is_owner(metadata, login):
        raise HTTPException(404, "thread not found")
    sandbox_id = metadata.get("sandbox_id")
    if ENV.SANDBOX_TYPE.get() != "langsmith" or metadata.get("sandbox_bridge_client"):
        raise HTTPException(409, "Environment configuration requires a cloud LangSmith sandbox")
    if not isinstance(sandbox_id, str) or not sandbox_id or sandbox_id == "__creating__":
        raise HTTPException(409, "Thread sandbox is not ready")
    sandbox_client = get_async_sandbox_client()
    try:
        async with engine().begin() as connection:
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:sandbox_id, 0))"),
                {"sandbox_id": sandbox_id},
            )
            sandbox = await sandbox_client.get_sandbox(sandbox_id)
            config = sandbox.run_config or RunConfig()
            variables = dict(config.env_vars or {})
            variables[body.name] = body.value.get_secret_value()
            await sandbox_client.update_sandbox(
                sandbox_id,
                run_config=RunConfig(
                    user=config.user, work_dir=config.work_dir, env_vars=variables
                ),
            )
    except Exception as exc:
        logger.warning(
            "Sandbox environment update failed", extra={"error_type": type(exc).__name__}
        )
        raise HTTPException(502, "Could not update sandbox environment") from None
    finally:
        await sandbox_client.aclose()

"""Saved sandbox apps: a person's named web apps, each served from a port of a thread's sandbox.

The app's code and data (typically a SQLite file) live in its ``workdir`` in the
sandbox, which keeps its filesystem while stopped. Its process does not survive
a stop, so launching reruns ``start_command`` whenever nothing answers on the port.
"""

import base64
import json
import logging
import posixpath
import re
from datetime import datetime
from functools import cache
from importlib import resources
from typing import TYPE_CHECKING, Self
from uuid import UUID, uuid7

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import ForeignKey, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base

if TYPE_CHECKING:
    from deepagents.backends.protocol import SandboxBackendProtocol

logger = logging.getLogger(__name__)

MAX_APP_NAME_CHARS = 64
MAX_APP_DESCRIPTION_CHARS = 500
MAX_START_COMMAND_CHARS = 2000
LAUNCH_TIMEOUT_SECONDS = 90
LOG_DIR = "/tmp/open-swe-apps"
_APP_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class AppLaunchError(RuntimeError):
    """The app's start command did not bring up a server on its port."""

    def __init__(self, message: str, log: str = "") -> None:
        super().__init__(message)
        self.log = log


class AppSandboxGoneError(AppLaunchError):
    """The sandbox the app was saved from has been deleted."""


@cache
def _launch_script() -> str:
    return resources.files("openswe.resources").joinpath("app_launch.py").read_text("utf-8")


class AppSpec(BaseModel):
    """How to serve an app: where it lives in the sandbox and how to start it."""

    name: str = Field(min_length=1, max_length=MAX_APP_NAME_CHARS)
    description: str = Field(default="", max_length=MAX_APP_DESCRIPTION_CHARS)
    port: int = Field(ge=1, le=65535)
    start_command: str = Field(min_length=1, max_length=MAX_START_COMMAND_CHARS)
    workdir: str = Field(min_length=1, max_length=1024)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        value = value.strip()
        if not _APP_NAME_RE.fullmatch(value):
            raise ValueError("name must use lowercase letters, numbers, and single hyphens")
        return value

    @field_validator("description", "start_command")
    @classmethod
    def _stripped(cls, value: str) -> str:
        return value.strip()

    @field_validator("workdir")
    @classmethod
    def _absolute_workdir(cls, value: str) -> str:
        value = value.strip()
        if not posixpath.isabs(value):
            raise ValueError("workdir must be an absolute path in the sandbox")
        return posixpath.normpath(value)

    def _launch_command(self) -> str:
        payload = {
            "port": self.port,
            "command": self.start_command,
            "workdir": self.workdir,
            "log_path": f"{LOG_DIR}/{self.name}.log",
        }
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()
        return f"python3 - <<'PY'\n{_launch_script().replace('__PAYLOAD__', encoded)}PY"

    async def start_in(self, sandbox: SandboxBackendProtocol) -> bool:
        """Ensure the app answers on its port, starting it if needed; ``True`` when started."""
        result = await sandbox.aexecute(self._launch_command(), timeout=LAUNCH_TIMEOUT_SECONDS)
        lines = (result.output or "").strip().splitlines()
        try:
            outcome = json.loads(lines[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            raise AppLaunchError("Could not run the app's launcher", result.output or "") from exc
        if "error" in outcome:
            raise AppLaunchError(outcome["error"], outcome.get("log", ""))
        return outcome.get("status") == "started"


class SandboxApp(Base):
    """A person's saved app, served from ``port`` of the sandbox it was built in."""

    __tablename__ = "sandbox_app"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str]
    description: Mapped[str]
    thread_id: Mapped[str]
    sandbox_id: Mapped[str]
    port: Mapped[int]
    start_command: Mapped[str]
    workdir: Mapped[str]
    url: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)

    @property
    def spec(self) -> AppSpec:
        return AppSpec(
            name=self.name,
            description=self.description,
            port=self.port,
            start_command=self.start_command,
            workdir=self.workdir,
        )

    @classmethod
    async def save(
        cls, user_id: UUID, spec: AppSpec, *, thread_id: str, sandbox_id: str, url: str
    ) -> Self:
        """Create the person's app named ``spec.name``, or repoint it at this sandbox."""
        values = {
            **spec.model_dump(),
            "thread_id": thread_id,
            "sandbox_id": sandbox_id,
            "url": url,
        }
        upsert = insert(cls).values(id=uuid7(), user_id=user_id, **values)
        async with postgres.session() as session:
            saved = await session.scalar(
                upsert.on_conflict_do_update(
                    index_elements=[cls.user_id, cls.name],
                    set_={**values, "updated_at": func.clock_timestamp()},
                )
                .returning(cls)
                .execution_options(populate_existing=True)
            )
        if saved is None:
            raise RuntimeError("saving the app returned no row")
        return saved

    @classmethod
    async def for_user(cls, user_id: UUID) -> list[Self]:
        async with postgres.session() as session:
            rows = await session.scalars(
                select(cls).where(cls.user_id == user_id).order_by(cls.updated_at.desc())
            )
            return list(rows)

    @classmethod
    async def owned(cls, user_id: UUID, app_id: UUID) -> Self | None:
        async with postgres.session() as session:
            return await session.scalar(select(cls).where(cls.id == app_id, cls.user_id == user_id))

    async def delete(self) -> None:
        async with postgres.session() as session:
            await session.execute(delete(type(self)).where(type(self).id == self.id))

    async def launch(self) -> bool:
        """Wake the app's sandbox and start the app unless it is already serving."""
        # deferred: pulls deepagents -> langchain_anthropic -> anthropic at import time
        from openswe.sandboxes.connect import connect_sandbox
        from openswe.sandboxes.providers.registry import SandboxGoneError

        try:
            sandbox = await connect_sandbox(self.sandbox_id, thread_id=self.thread_id)
        except SandboxGoneError as exc:
            raise AppSandboxGoneError("The sandbox this app was built in was deleted") from exc
        except Exception as exc:
            logger.exception("Could not connect to app sandbox", extra={"sandbox": self.sandbox_id})
            raise AppLaunchError("Could not reach the sandbox this app runs in") from exc
        started = await self.spec.start_in(sandbox)
        logger.info(
            "Launched sandbox app",
            extra={"app_id": str(self.id), "sandbox": self.sandbox_id, "started": started},
        )
        return started

"""The MDA connections the run's operations read, and what a call carries of them.

Where a backend setting names a connection's slug, the catalog names it in the spec of
each operation whose tool reads it (``ToolSpec.connections``). The MDA runtime reads
it as the run and sends its value with each call (``RunOperation.credentials``); the
tool reads it here, for that call alone. Nothing keeps it.
"""

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import cache
from types import MappingProxyType
from typing import Final, Literal

from langchain_core.runnables import RunnableConfig
from pydantic import ValidationError
from runtime_api.connections import ConnectionRef

from openswe.config import ENV, EnvVar
from openswe.run_config import RunConfig

logger = logging.getLogger(__name__)

#: Run metadata naming the Open SWE user a run acts as, whose own connections it reads.
#: Only the backend writes it, and only for a run that person started: a channel's run,
#: which MDA starts as its verified sender, or one they started from the dashboard on a
#: channel's thread, which the thread router starts as their principal. The run's
#: ``user_id`` is whom it is attributed to, which is not whom it acts as.
ACTING_USER_KEY: Final = "acting_user_id"

_carried: ContextVar[Mapping[str, str]] = ContextVar(
    "operation_credentials", default=MappingProxyType({})
)


@dataclass(frozen=True)
class OperationConnection:
    """A connection operations read once this backend's ``setting`` names its slug."""

    setting: EnvVar
    owner: Literal["agent", "user"]

    def ref(self) -> ConnectionRef | None:
        slug = self.setting.optional()
        return None if slug is None else _ref(self.setting.name, slug, self.owner)

    def credential(self) -> str | None:
        """What the runtime read of it for the operation in progress; None when it sent none."""
        ref = self.ref()
        return None if ref is None else _carried.get().get(ref.slug)


EXA: Final = OperationConnection(ENV.MDA_EXA_CONNECTION, "agent")
GITHUB_USER: Final = OperationConnection(ENV.MDA_GITHUB_USER_CONNECTION, "user")

_READ_BY: Final[Mapping[str, tuple[OperationConnection, ...]]] = {
    "web_search": (EXA,),
    "open_pull_request": (GITHUB_USER,),
}


def operation_connections(operation: str, config: RunnableConfig) -> list[ConnectionRef]:
    """The connections ``operation``'s tool reads in the run ``config`` configures, as this
    backend's settings name them.

    A person's own only when the run acts as one (``ACTING_USER_KEY``), whatever person it
    is attributed to. Any other run has no person's principal: MDA starts it as a service
    principal, MDA's channel one (``mda:channel``) on a channel's thread and the backend
    key's own on any other, and MDA refuses user connections to a service principal, so
    a grant there can be neither made nor read.
    """
    for_person = acting_user(config.get("metadata") or {}) is not None
    return [
        ref
        for read in _READ_BY.get(operation, ())
        if not (read is GITHUB_USER and RunConfig.from_config(config).auth_provider == "langsmith")
        and (ref := read.ref()) is not None
        and (ref.owner == "agent" or for_person)
    ]


def acting_user(metadata: Mapping[str, object]) -> str | None:
    """The Open SWE user the run ``metadata`` describes acts as (``ACTING_USER_KEY``)."""
    user_id = metadata.get(ACTING_USER_KEY)
    return user_id if isinstance(user_id, str) and user_id.strip() else None


@cache
def _ref(setting: str, slug: str, owner: Literal["agent", "user"]) -> ConnectionRef | None:
    """The connection ``setting`` names, or None when MDA could not read it, said once."""
    try:
        return ConnectionRef(slug=slug, owner=owner)
    except ValidationError:
        logger.warning(
            "A setting names a connection MDA cannot read; operations go without it",
            extra={"setting": setting, "connection": slug},
            exc_info=True,
        )
        return None


@contextmanager
def carrying(credentials: Mapping[str, str]) -> Iterator[None]:
    """Hand ``credentials`` to the operation run inside, and to nothing after it."""
    token = _carried.set(credentials)
    try:
        yield
    finally:
        _carried.reset(token)

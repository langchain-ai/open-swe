"""The shape of ``configurable`` — the per-run contract every graph reads.

``configurable`` rides in the ``RunnableConfig`` of every run. It is assembled by
webhooks, dashboards, and cron launchers, merged and re-written at several hops,
and then read in dozens of modules.

Three rules govern it:

**Unknown keys survive.** Writers add keys this module has never heard of, and
call sites read a configurable, add to it, and pass it on. ``extra="allow"``
plus :meth:`RunConfig.dump` (which excludes unset fields) keeps that round-trip
byte-identical. Platform subclasses declare their own keys; a configurable
parsed by this base class round-trips them unchanged as extras.

**Parsing never raises, and never loses more than it has to.** Nothing validates
``configurable`` on write, so a single malformed value must not cost the run its
``thread_id``. A field that fails validation is dropped and the rest is kept.

**Everything is optional.** Which keys are present depends on the graph and the
trigger.
"""

import logging
from collections.abc import Mapping
from typing import Annotated, Any, Self

from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError

logger = logging.getLogger(__name__)


def _reject_bool(value: Any) -> Any:
    """Bools are ints to pydantic, so ``pr_number=True`` would silently mean PR 1."""
    if isinstance(value, bool):
        raise ValueError("bool is not a valid integer here")
    return value


Int = Annotated[int, BeforeValidator(_reject_bool)]


class RunConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    # Identity and provenance
    thread_id: str | None = None
    run_id: str | None = None
    prepare_run_id: str | None = None
    source: str | None = None
    task: str | None = None
    environment: str | None = None
    local_project_path: str | None = None

    # Model selection
    agent_model_id: str | None = None
    agent_effort: str | None = None

    # Behavior toggles
    plan_mode: bool | None = None
    stop_summary: bool | None = None

    # Eval harness
    eval: bool | None = None

    # Background jobs
    watch_key: str | None = None
    schedule_id: str | None = None
    background_task_completion: bool | None = None

    @classmethod
    def parse(cls, raw: Any) -> Self:
        """Parse a ``configurable`` mapping, dropping only the fields that fail."""
        if isinstance(raw, cls):
            return raw
        if not isinstance(raw, Mapping):
            return cls()
        data = dict(raw)
        for _ in range(len(cls.model_fields) + 1):
            try:
                return cls.model_validate(data)
            except ValidationError as exc:
                dropped = {
                    str(error["loc"][0])
                    for error in exc.errors()
                    if error.get("loc") and str(error["loc"][0]) in data
                }
                if not dropped:
                    logger.warning("Unparseable configurable, ignoring", exc_info=True)
                    return cls()
                logger.warning("Dropping unparseable configurable keys: %s", sorted(dropped))
                for key in dropped:
                    del data[key]
        return cls()

    @classmethod
    def from_config(cls, config: Any) -> Self:
        """Parse the ``configurable`` out of a ``RunnableConfig``."""
        if not isinstance(config, Mapping):
            return cls()
        return cls.parse(config.get("configurable"))

    @classmethod
    def from_runtime(cls) -> Self:
        """Parse the running graph's own ``configurable``."""
        return cls.from_config(get_config())

    @classmethod
    def from_tool_request(cls, request: ToolCallRequest) -> Self:
        """Parse the configurable a tool call is running under."""
        runtime_config = getattr(getattr(request, "runtime", None), "config", None)
        if isinstance(runtime_config, Mapping):
            return cls.from_config(runtime_config)
        try:
            return cls.from_runtime()
        except Exception:
            logger.debug("No runnable config available for tool call", exc_info=True)
            return cls()

    def dump(self) -> dict[str, Any]:
        """The JSON value to store, preserving exactly the keys that were set."""
        return self.model_dump(mode="json", exclude_unset=True)

    def get(self, key: str) -> Any:
        """Value for ``key``, whether it is a declared field or an extra."""
        if key in type(self).model_fields:
            return getattr(self, key)
        return (self.model_extra or {}).get(key)

    @property
    def is_eval(self) -> bool:
        return self.eval is True

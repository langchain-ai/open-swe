"""LangSmith Harbor plugin with current workspace routing headers."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from harbor_langsmith.plugin import LangSmithPlugin as _HarborLangSmithPlugin

from mda_harbor import _job_artifact

if TYPE_CHECKING:
    from harbor.job import Job
    from harbor.trial.hooks import TrialHookEvent


_LEGACY_WORKSPACE_HEADER = "LANGSMITH-WORKSPACE-ID"
_TENANT_HEADER = "X-Tenant-Id"
_MDA_AGENT_IMPORT_PATH = "mda_harbor.agent:ManagedDeepAgent"


class LangSmithPlugin(_HarborLangSmithPlugin):
    """Route workspace-scoped LangSmith requests with ``X-Tenant-Id``."""

    def _setup(self, job: Job) -> None:
        workspace_id = self.workspace_id
        if not workspace_id:
            super()._setup(job)
            return

        self._session.headers.pop(_LEGACY_WORKSPACE_HEADER, None)
        self._session.headers[_TENANT_HEADER] = workspace_id
        self.workspace_id = None
        try:
            super()._setup(job)
        finally:
            self.workspace_id = workspace_id
            self._session.headers.pop(_LEGACY_WORKSPACE_HEADER, None)

    def _handle_event_sync(self, event: TrialHookEvent) -> None:
        binding = _job_artifact(event.config.job_id)
        agent = event.config.agent
        if (
            binding is not None
            and binding.model_name is not None
            and agent.import_path == _MDA_AGENT_IMPORT_PATH
            and (not isinstance(agent.model_name, str) or not agent.model_name.strip())
        ):
            event = copy.deepcopy(event)
            event.config.agent.model_name = binding.model_name
        super()._handle_event_sync(event)

import os
import shlex
from typing import Annotated

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.agents.options import Cli, InstalledAgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

RELEASES = "https://github.com/langchain-ai/open-swe/releases/latest/download"
FORWARDED_ENV = ("OPEN_SWE_API_KEY", "OPEN_SWE_BACKEND_URL")


class OpenSWEOptions(InstalledAgentOptions):
    effort: Annotated[str | None, Cli("--effort")] = None


class OpenSWE(BaseInstalledAgent):
    """Runs a cloud Open SWE agent with the task container as its sandbox via `oswe run`."""

    options_model = OpenSWEOptions

    @staticmethod
    def name() -> str:
        return "open-swe"

    def get_version_command(self) -> str:
        return "oswe --version"

    async def install(self, environment: BaseEnvironment) -> None:
        await self.ensure_system_dependencies(environment, ("curl", "tar", "git"))
        arch = '$(uname -m | sed "s/x86_64/x64/;s/aarch64/arm64/")'
        await self.exec_as_root(
            environment,
            f"curl -fsSL {RELEASES}/oswe-linux-{arch}.tar.gz | tar -xz -C /usr/local/bin oswe",
        )

    def populate_context_post_run(self, context: AgentContext) -> None:
        pass

    @with_prompt_template
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        model = (
            f"--model {shlex.quote(self.model_name.replace('/', ':', 1))} "
            if self.model_name
            else ""
        )
        await self.exec_as_agent(
            environment,
            f"oswe run {model}{self.build_cli_flags()} {shlex.quote(instruction)} "
            "2>&1 | tee /logs/agent/oswe.txt",
            env={key: os.environ[key] for key in FORWARDED_ENV if key in os.environ},
        )

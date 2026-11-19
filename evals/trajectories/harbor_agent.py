"""Harbor agent that runs Robin on a task, with the task's container as its sandbox.

It installs Robin's CLI (``oswe``, from the desktop release) in the container and runs
``oswe run "<task>"`` there, so the deployed agent works on the container through the
sandbox bridge. Ported from Johannes's Terminal-Bench harness (``open-swe/terminal-bench``).
"""

import asyncio
import os
import shlex
from typing import Annotated

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.agents.options import Cli, InstalledAgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

RELEASE = "https://github.com/langchain-ai/open-swe/releases/download/desktop-v0.3.19"
# From that release's oswe-SHA256SUMS; update them with the version.
CHECKSUMS = {
    "x64": "9c87634341356510d2fe5532566e136c443bf305c5b74c1df9656f32e24c83c9",
    "arm64": "a09f102f7a92c476c66320b4d6b6cad18ba2364e6eb8ab3e10c41470f1042216",
}
# An API key, or in CI the OIDC broker's port and secret (``oidc_broker.py``). The job's
# own token-request credentials never enter the container, where Robin's commands run.
FORWARDED_ENV = (
    "OPEN_SWE_API_KEY",
    "OPEN_SWE_BACKEND_URL",
    "ROBIN_OIDC_BROKER_PORT",
    "ROBIN_OIDC_BROKER_TOKEN",
)
# Points the CLI's GitHub Actions credential at the broker on the runner, reached at the
# container's default gateway.
USE_BROKER = """
if [ -n "$ROBIN_OIDC_BROKER_PORT" ]; then
  route=$(awk '$2 == "00000000" {print $3; exit}' /proc/net/route)
  gateway=$(printf '%d.%d.%d.%d' 0x$(echo "$route" | cut -c7-8) 0x$(echo "$route" | cut -c5-6) \
    0x$(echo "$route" | cut -c3-4) 0x$(echo "$route" | cut -c1-2))
  export ACTIONS_ID_TOKEN_REQUEST_URL="http://$gateway:$ROBIN_OIDC_BROKER_PORT/token"
  export ACTIONS_ID_TOKEN_REQUEST_TOKEN="$ROBIN_OIDC_BROKER_TOKEN"
fi
"""
STOP = "pkill -INT -x oswe; timeout 30 sh -c 'while pgrep -x oswe; do sleep 1; done' || true"


class RobinOptions(InstalledAgentOptions):
    effort: Annotated[str | None, Cli("--effort")] = None


class Robin(BaseInstalledAgent):
    """Runs the deployed Robin agent on the task through ``oswe run``."""

    options_model = RobinOptions

    @staticmethod
    def name() -> str:
        return "robin"

    def get_version_command(self) -> str:
        return "oswe --version"

    async def install(self, environment: BaseEnvironment) -> None:
        await self.ensure_system_dependencies(environment, ("curl", "tar", "git", "procps"))
        arch = ((await environment.exec("uname -m")).stdout or "").strip()
        arch = {"x86_64": "x64", "aarch64": "arm64"}.get(arch, arch)
        await self.exec_as_root(
            environment,
            f"curl -fsSL -o /tmp/oswe.tar.gz {RELEASE}/oswe-linux-{arch}.tar.gz"
            f" && echo '{CHECKSUMS[arch]}  /tmp/oswe.tar.gz' | sha256sum -c -"
            " && tar -xzf /tmp/oswe.tar.gz -C /usr/local/bin oswe",
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
        try:
            # Keeps oswe's own exit status, which a pipe into tee would replace.
            await self.exec_as_agent(
                environment,
                f"{USE_BROKER}oswe run {model}{self.build_cli_flags()} {shlex.quote(instruction)} "
                "> /logs/agent/oswe.txt 2>&1; status=$?; cat /logs/agent/oswe.txt; exit $status",
                env={key: os.environ[key] for key in FORWARDED_ENV if key in os.environ},
            )
        except asyncio.CancelledError:
            await self.exec_as_root(environment, STOP)
            raise

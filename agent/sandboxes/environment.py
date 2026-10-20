"""Observed execution capabilities, collected inside the selected sandbox."""

import json
import logging
from typing import Literal

from deepagents.backends.protocol import SandboxBackendProtocol
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

_PYTHON_PROBE = (
    ' -c "import json,os,platform,sys; '
    "print(json.dumps(dict(system=platform.system(),"
    "shell_family='cmd' if os.name=='nt' else 'posix',"
    "python_version=platform.python_version(),python_executable=sys.executable,"
    'working_dir=os.getcwd())))"'
)


class ExecutionEnvironment(BaseModel):
    system: str = "unknown"
    shell_family: Literal["cmd", "posix", "unknown"] = "unknown"
    python_version: str | None = None
    python_executable: str | None = None
    working_dir: str | None = None
    git_version: str | None = None
    probe_errors: list[str] = Field(default_factory=list)


async def probe_execution_environment(backend: SandboxBackendProtocol) -> ExecutionEnvironment:
    errors: list[str] = []
    environment = ExecutionEnvironment()
    for executable in ("python", "python3"):
        try:
            response = await backend.aexecute(executable + _PYTHON_PROBE, timeout=10)
            if response.exit_code != 0:
                errors.append(f"{executable}: exit {response.exit_code}: {response.output[-1000:]}")
                continue
            environment = ExecutionEnvironment.model_validate(json.loads(response.output.strip()))
            break
        except (ValueError, ValidationError) as exc:
            logger.warning("Invalid sandbox environment response", extra={"probe": executable})
            errors.append(f"{executable}: {type(exc).__name__}: {exc}")
    response = await backend.aexecute("git --version", timeout=10)
    if response.exit_code == 0:
        environment.git_version = response.output.strip()
    else:
        errors.append(f"git: exit {response.exit_code}: {response.output[-1000:]}")
    environment.probe_errors = errors
    return environment

from daytona import CreateSandboxFromSnapshotParams, Daytona, DaytonaConfig
from langchain_daytona import DaytonaSandbox

from agent.config import ENV


def _get_daytona_sandbox_params() -> CreateSandboxFromSnapshotParams:
    return CreateSandboxFromSnapshotParams(snapshot=ENV.DAYTONA_SANDBOX_SNAPSHOT.get())


def create_daytona_sandbox(sandbox_id: str | None = None):
    api_key = ENV.DAYTONA_API_KEY.optional()
    if not api_key:
        raise ValueError("DAYTONA_API_KEY environment variable is required")

    daytona = Daytona(config=DaytonaConfig(api_key=api_key))

    if sandbox_id:
        sandbox = daytona.get(sandbox_id)
    else:
        sandbox = daytona.create(params=_get_daytona_sandbox_params())

    return DaytonaSandbox(sandbox=sandbox)

from daytona import CreateSandboxFromSnapshotParams, Daytona, DaytonaConfig
from langchain_daytona import DaytonaSandbox

from ..config import daytona_api_key, daytona_snapshot

DEFAULT_DAYTONA_SANDBOX_SNAPSHOT = "daytonaio/sandbox:0.6.0"


def _get_daytona_sandbox_params() -> CreateSandboxFromSnapshotParams:
    return CreateSandboxFromSnapshotParams(
        snapshot=daytona_snapshot(DEFAULT_DAYTONA_SANDBOX_SNAPSHOT)
    )


def create_daytona_sandbox(sandbox_id: str | None = None):
    api_key = daytona_api_key()
    if not api_key:
        raise ValueError("DAYTONA_API_KEY environment variable is required")

    daytona = Daytona(config=DaytonaConfig(api_key=api_key))

    if sandbox_id:
        sandbox = daytona.get(sandbox_id)
    else:
        sandbox = daytona.create(params=_get_daytona_sandbox_params())

    return DaytonaSandbox(sandbox=sandbox)

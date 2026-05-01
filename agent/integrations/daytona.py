import os

from daytona import CreateSandboxFromSnapshotParams, Daytona, DaytonaConfig
from langchain_daytona import DaytonaSandbox

# TODO: Update this to include your specific sandbox configuration
DAYTONA_SANDBOX_PARAMS = CreateSandboxFromSnapshotParams(snapshot="daytonaio/sandbox:0.6.0")


def create_daytona_sandbox(sandbox_id: str | None = None):
    api_key = os.getenv("DAYTONA_API_KEY")
    if not api_key:
        raise ValueError("DAYTONA_API_KEY environment variable is required")

    daytona = Daytona(config=DaytonaConfig(api_key=api_key))

    if sandbox_id:
        sandbox = daytona.get(sandbox_id)
    else:
        sandbox = daytona.create(params=DAYTONA_SANDBOX_PARAMS)

    return DaytonaSandbox(sandbox=sandbox)

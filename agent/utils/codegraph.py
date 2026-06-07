import logging

from deepagents.backends.protocol import SandboxBackendProtocol

logger = logging.getLogger(__name__)


def ensure_codegraph_installed(sandbox: SandboxBackendProtocol) -> bool:
    """Check if codegraph is installed, and if not, install it globally using npm."""
    try:
        # Check version
        res = sandbox.execute("codegraph --version")
        if res.exit_code == 0:
            return True

        logger.info("CodeGraph CLI not found in sandbox. Installing globally via npm...")
        # Run npm install
        res = sandbox.execute("npm install -g @colbymchenry/codegraph")
        if res.exit_code == 0:
            logger.info("CodeGraph CLI installed successfully.")
            return True
        else:
            logger.warning("Failed to install CodeGraph via npm. Output: %s", res.output)
            return False
    except Exception as e:
        logger.warning("Error verifying/installing CodeGraph in sandbox: %s", e)
        return False


def ensure_codegraph_indexed(sandbox: SandboxBackendProtocol, work_dir: str) -> bool:
    """Ensure that CodeGraph is initialized and indexed in the workspace directory."""
    if not ensure_codegraph_installed(sandbox):
        return False

    try:
        # Check if database file exists (.codegraph/codegraph.db)
        # Using a simple python check inside the sandbox
        check_cmd = (
            f"python -c \"import os; print(os.path.exists('{work_dir}/.codegraph/codegraph.db'))\""
        )
        res = sandbox.execute(check_cmd)
        if res.exit_code == 0 and res.output.strip() == "True":
            # Database exists, run sync to ensure it is fresh
            logger.info("CodeGraph database found. Syncing changes...")
            sync_res = sandbox.execute(f"cd {work_dir} && codegraph sync")
            return sync_res.exit_code == 0

        logger.info("CodeGraph database not found. Initializing and indexing...")
        init_res = sandbox.execute(f"cd {work_dir} && codegraph init && codegraph index")
        return init_res.exit_code == 0
    except Exception as e:
        logger.warning("Failed to index codebase using CodeGraph: %s", e)
        return False


def get_affected_tests_via_codegraph(
    sandbox: SandboxBackendProtocol, work_dir: str
) -> list[str] | None:
    """Determine which test files are affected by changed source files using codegraph affected."""
    if not ensure_codegraph_indexed(sandbox, work_dir):
        return None

    try:
        # Run git diff piped to codegraph affected
        res = sandbox.execute(
            f"cd {work_dir} && git diff --name-only | codegraph affected --stdin --quiet"
        )
        if res.exit_code == 0:
            files = [f.strip() for f in res.output.strip().split("\n") if f.strip()]
            logger.info("CodeGraph resolved affected tests: %s", files)
            return files
        else:
            logger.warning("codegraph affected failed: %s", res.output)
            return None
    except Exception as e:
        logger.warning("Error getting affected tests via CodeGraph: %s", e)
        return None


def get_workspace_files_via_codegraph(
    sandbox: SandboxBackendProtocol, work_dir: str
) -> list[str] | None:
    """Get list of workspace files using codegraph files."""
    if not ensure_codegraph_indexed(sandbox, work_dir):
        return None

    try:
        res = sandbox.execute(f"cd {work_dir} && codegraph files --quiet")
        if res.exit_code == 0:
            files = [f.strip() for f in res.output.strip().split("\n") if f.strip()]
            # Normalize paths to use forward slashes
            files = [f.replace("\\", "/") for f in files]
            logger.info("CodeGraph listed %d workspace files", len(files))
            return files
        else:
            logger.warning("codegraph files failed: %s", res.output)
            return None
    except Exception as e:
        logger.warning("Error listing files via CodeGraph: %s", e)
        return None

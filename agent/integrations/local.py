import os
from pathlib import Path

from deepagents.backends import LocalShellBackend

SANDBOX_GITCONFIG = ".gitconfig-sandbox"
LOCAL_SHELL_ENV_EXCLUDE = {
    "ANTHROPIC_API_KEY",
    "BASETEN_API_KEY",
    "FIREWORKS_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "LANGSMITH_API_KEY",
    "OPEN_SWE_OPENAI_OAUTH_ACCOUNT_FILE",
    "OPEN_SWE_OPENAI_OAUTH_BROKER_TOKEN",
    "OPEN_SWE_OPENAI_OAUTH_BROKER_URL",
    "OPENAI_API_KEY",
}


def _scoped_git_config_env(root_dir: str) -> dict[str, str]:
    """Point `git config --global` at a sandbox-local file.

    Local sandboxes run on the host, so the bot identity every run writes would
    otherwise overwrite the developer's own `~/.gitconfig` user.name/email. The
    scoped file includes the real one so credential helpers and aliases survive.
    """
    scoped = Path(root_dir) / SANDBOX_GITCONFIG
    if not scoped.exists():
        host = Path.home() / ".gitconfig"
        scoped.write_text(f"[include]\n\tpath = {host}\n" if host.exists() else "")
    return {"GIT_CONFIG_GLOBAL": str(scoped)}


def create_local_sandbox(sandbox_id: str | None = None):
    """Create a local shell sandbox with no isolation.

    WARNING: This runs commands directly on the host machine with no sandboxing.
    Only use for local development with human-in-the-loop enabled.

    The root directory defaults to the current working directory and can be
    overridden via the LOCAL_SANDBOX_ROOT_DIR environment variable. It is
    created if it does not already exist.

    Args:
        sandbox_id: Ignored for local sandboxes; accepted for interface compatibility.

    Returns:
        LocalShellBackend instance implementing SandboxBackendProtocol.
    """
    root_dir = os.getenv("LOCAL_SANDBOX_ROOT_DIR", os.getcwd())
    os.makedirs(root_dir, exist_ok=True)

    env = {key: value for key, value in os.environ.items() if key not in LOCAL_SHELL_ENV_EXCLUDE}
    if not os.getenv("GIT_CONFIG_GLOBAL"):
        env.update(_scoped_git_config_env(root_dir))

    return LocalShellBackend(
        root_dir=root_dir,
        virtual_mode=True,
        inherit_env=False,
        env=env,
    )

Change how one of a workspace's repositories is configured inside that workspace.

Belonging to a workspace says the agent may work in a repository. These
settings say what the repository itself may do, and they are deliberately
separate, so binding one never grants it anything. A repository that is not
bound to the workspace cannot be configured; bind it with
``publish_workspace`` first.

Settings you leave out keep their current value.

Args:
    workspace: Name of the workspace the repository belongs to.
    repo: The repository as ``owner/name``.
    may_start_threads: Whether a GitHub Actions workflow in this repository
        may start threads in the workspace with the OIDC token it issues
        itself, and no stored secret. Such a run belongs to the workspace and
        to nobody, so it never borrows a person's credentials. Off until an
        admin turns it on.

Returns:
    ``{"ok": true, "workspace": ..., "repository": {...}}`` with the settings
    as they now stand, or ``ok: false`` with the reason it was refused.

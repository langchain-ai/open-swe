Grant exactly these repositories the right to start threads in a workspace by themselves.

A GitHub Actions workflow can authenticate to Open SWE with the OIDC token it
issues itself, with no stored secret. This grant is what such a token is
checked against: a workflow gets in only where an admin bound its repository to
a workspace *and* named it here. A run started that way belongs to the
workspace and to nobody, so it never borrows a person's credentials.

The list replaces whatever was granted before, so pass every repository that
should keep the right, and pass an empty list to revoke them all. Repositories
that are not bound to the workspace are ignored rather than bound, so this can
never widen which repositories the workspace owns.

Args:
    name: Name of the workspace.
    repos: Every ``owner/name`` that may start threads there, replacing the
        previous grant.

Returns:
    ``{"ok": true, "workspace": ..., "thread_starters": [...]}`` with what is
    granted now, or ``ok: false`` with the reason it was refused.

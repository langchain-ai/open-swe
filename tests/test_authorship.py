from __future__ import annotations

from agent.utils import authorship


def test_github_source_prefers_config_identity_without_token_lookup(monkeypatch) -> None:
    def fail_token_lookup(_github_token: str | None):
        raise AssertionError("GitHub source should not need /user lookups for App installation tokens")

    monkeypatch.setattr(authorship, "_identity_from_github_token", fail_token_lookup)

    identity = authorship.resolve_triggering_user_identity(
        {
            "configurable": {
                "source": "github",
                "github_login": "parimple",
                "github_user_id": 123,
            }
        },
        github_token="installation-token",
    )

    assert identity == authorship.CollaboratorIdentity(
        display_name="parimple",
        commit_name="parimple",
        commit_email="123+parimple@users.noreply.github.com",
    )


def test_non_github_source_still_prefers_token_identity(monkeypatch) -> None:
    monkeypatch.setattr(
        authorship,
        "_identity_from_github_token",
        lambda github_token: authorship.CollaboratorIdentity(
            display_name="Patryk Pyzel",
            commit_name="Patryk Pyzel",
            commit_email="123+parimple@users.noreply.github.com",
        ),
    )
    monkeypatch.setattr(
        authorship,
        "_identity_from_config",
        lambda config: authorship.CollaboratorIdentity(
            display_name="fallback-user",
            commit_name="fallback-user",
            commit_email="fallback@example.com",
        ),
    )

    identity = authorship.resolve_triggering_user_identity(
        {"configurable": {"source": "slack"}},
        github_token="user-token",
    )

    assert identity == authorship.CollaboratorIdentity(
        display_name="Patryk Pyzel",
        commit_name="Patryk Pyzel",
        commit_email="123+parimple@users.noreply.github.com",
    )

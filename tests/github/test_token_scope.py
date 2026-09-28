import pytest

from agent.github.token_scope import (
    GITHUB_TOKEN_REPOSITORIES_KEY,
    event_token_repositories,
    token_repositories_from_metadata,
)


@pytest.mark.parametrize(
    ("private", "expected"),
    [(True, None), (False, ["acme/oss"]), (None, ["acme/oss"])],
)
def test_only_a_known_private_repository_keeps_the_installation_token(
    private: bool | None, expected: list[str] | None
) -> None:
    assert event_token_repositories("acme", "oss", private=private) == expected


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({}, None),
        ({GITHUB_TOKEN_REPOSITORIES_KEY: ["acme/oss"]}, ["acme/oss"]),
        ({GITHUB_TOKEN_REPOSITORIES_KEY: None}, []),
        ({GITHUB_TOKEN_REPOSITORIES_KEY: "acme/oss"}, []),
        ({GITHUB_TOKEN_REPOSITORIES_KEY: ["acme/oss", 7]}, []),
    ],
)
def test_an_unreadable_recorded_scope_grants_nothing(
    metadata: dict[str, object], expected: list[str] | None
) -> None:
    assert token_repositories_from_metadata(metadata) == expected

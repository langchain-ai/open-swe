import pytest

from openswe.github.repo_names import controlling_org, display_repo_name, normalize_repo_full_name


def test_normalize_repo_full_name_accepts_urls() -> None:
    assert normalize_repo_full_name("https://github.com/langchain-ai/langgraph") == (
        "langchain-ai/langgraph"
    )


def test_normalize_repo_full_name_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        normalize_repo_full_name("not-a-repo")


@pytest.mark.parametrize(
    "orgs, expected", [("Acme", "repo"), ("acme,other", "ACME/repo"), ("", "ACME/repo")]
)
def test_repo_display_preserves_ambiguous_and_external_owners(
    monkeypatch: pytest.MonkeyPatch, orgs: str, expected: str
) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", orgs)
    assert display_repo_name("ACME/repo") == expected
    assert display_repo_name("external/repo") == "external/repo"
    assert display_repo_name("repo") == "repo"
    assert controlling_org() == ("acme" if orgs == "Acme" else None)

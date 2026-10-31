"""Tests for openswe.utils.repo."""

from openswe.utils.repo import extract_repo_from_text


class TestExtractRepoFromText:
    def test_explicit_repo_beats_github_url(self) -> None:
        result = extract_repo_from_text(
            "see https://github.com/langchain-ai/langgraph-api but use repo:my-org/my-repo"
        )
        assert result == {"owner": "my-org", "name": "my-repo"}

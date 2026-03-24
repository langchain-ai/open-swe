"""Tests for GitLab provider support (git_provider.py, gitlab.py, auth, tools)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.tools.github_comment import github_comment
from agent.utils import auth as auth_module
from agent.utils.auth import resolve_git_token
from agent.utils.git_provider import (
    GITHUB,
    GITLAB,
    get_clone_url,
    get_credential_url,
    get_git_host,
    get_git_provider,
    get_gitlab_host,
    get_gitlab_project_path,
    get_mr_or_pr_label,
    get_noreply_email,
)
from agent.utils.gitlab import (
    create_gitlab_mr,
    get_gitlab_default_branch,
    post_gitlab_note,
)

# ---------------------------------------------------------------------------
# git_provider helpers
# ---------------------------------------------------------------------------


class TestGetGitProvider:
    def test_defaults_to_github(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GIT_PROVIDER", raising=False)
        assert get_git_provider() == GITHUB

    def test_reads_gitlab_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GIT_PROVIDER", "gitlab")
        assert get_git_provider() == GITLAB

    def test_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GIT_PROVIDER", "GITLAB")
        assert get_git_provider() == GITLAB

    def test_unknown_falls_back_to_github(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GIT_PROVIDER", "bitbucket")
        assert get_git_provider() == GITHUB


class TestGetGitlabHost:
    def test_defaults_to_gitlab_com(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GITLAB_HOST", raising=False)
        assert get_gitlab_host() == "gitlab.com"

    def test_reads_custom_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITLAB_HOST", "git.example.com")
        assert get_gitlab_host() == "git.example.com"


class TestGetCloneUrl:
    def test_github_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GIT_PROVIDER", "github")
        assert get_clone_url("my-org", "my-repo") == "https://github.com/my-org/my-repo.git"

    def test_gitlab_url_default_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GIT_PROVIDER", "gitlab")
        monkeypatch.delenv("GITLAB_HOST", raising=False)
        assert get_clone_url("my-group", "my-project") == "https://gitlab.com/my-group/my-project.git"

    def test_gitlab_url_custom_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GIT_PROVIDER", "gitlab")
        monkeypatch.setenv("GITLAB_HOST", "git.dtok.io")
        assert get_clone_url("dtok-app", "dtok-app") == "https://git.dtok.io/dtok-app/dtok-app.git"


class TestGetCredentialUrl:
    def test_github_credential(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GIT_PROVIDER", "github")
        assert get_credential_url("ghp_token") == "https://git:ghp_token@github.com\n"

    def test_gitlab_credential_default_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GIT_PROVIDER", "gitlab")
        monkeypatch.delenv("GITLAB_HOST", raising=False)
        assert get_credential_url("glpat-token") == "https://oauth2:glpat-token@gitlab.com\n"

    def test_gitlab_credential_custom_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GIT_PROVIDER", "gitlab")
        monkeypatch.setenv("GITLAB_HOST", "git.dtok.io")
        assert get_credential_url("glpat-token") == "https://oauth2:glpat-token@git.dtok.io\n"


class TestGetGitHost:
    def test_github_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GIT_PROVIDER", "github")
        assert get_git_host() == "github.com"

    def test_gitlab_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GIT_PROVIDER", "gitlab")
        monkeypatch.setenv("GITLAB_HOST", "git.dtok.io")
        assert get_git_host() == "git.dtok.io"


class TestGetNoreplyEmail:
    def test_github_email(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GIT_PROVIDER", "github")
        assert get_noreply_email() == "open-swe@users.noreply.github.com"

    def test_gitlab_email_default_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GIT_PROVIDER", "gitlab")
        monkeypatch.delenv("GITLAB_HOST", raising=False)
        assert get_noreply_email() == "open-swe@users.noreply.gitlab.com"

    def test_gitlab_email_custom_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GIT_PROVIDER", "gitlab")
        monkeypatch.setenv("GITLAB_HOST", "git.dtok.io")
        assert get_noreply_email() == "open-swe@users.noreply.git.dtok.io"


class TestGetGitlabProjectPath:
    def test_encodes_slash(self) -> None:
        assert get_gitlab_project_path("my-group", "my-project") == "my-group%2Fmy-project"

    def test_encodes_nested_namespace(self) -> None:
        assert get_gitlab_project_path("dtok-app", "dtok-core-service") == "dtok-app%2Fdtok-core-service"


class TestGetMrOrPrLabel:
    def test_github_returns_pull_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GIT_PROVIDER", "github")
        assert get_mr_or_pr_label() == "Pull Request"

    def test_gitlab_returns_merge_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GIT_PROVIDER", "gitlab")
        assert get_mr_or_pr_label() == "Merge Request"


# ---------------------------------------------------------------------------
# gitlab.py API functions
# ---------------------------------------------------------------------------


class TestCreateGitlabMr:
    def test_creates_mr_successfully(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITLAB_HOST", "gitlab.com")

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "web_url": "https://gitlab.com/my-group/my-project/-/merge_requests/42",
            "iid": 42,
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            url, iid, existing = asyncio.run(
                create_gitlab_mr(
                    repo_owner="my-group",
                    repo_name="my-project",
                    gitlab_token="glpat-token",
                    title="Fix bug",
                    head_branch="open-swe/abc123",
                    base_branch="main",
                    body="Description",
                )
            )

        assert url == "https://gitlab.com/my-group/my-project/-/merge_requests/42"
        assert iid == 42
        assert existing is False

    def test_returns_existing_mr_on_conflict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITLAB_HOST", "gitlab.com")

        conflict_response = MagicMock()
        conflict_response.status_code = 409
        conflict_response.json.return_value = {"message": "Another open merge request already exists"}

        search_response = MagicMock()
        search_response.status_code = 200
        search_response.json.return_value = [
            {
                "web_url": "https://gitlab.com/my-group/my-project/-/merge_requests/7",
                "iid": 7,
            }
        ]

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=conflict_response)
            mock_client.get = AsyncMock(return_value=search_response)
            mock_client_cls.return_value = mock_client

            url, iid, existing = asyncio.run(
                create_gitlab_mr(
                    repo_owner="my-group",
                    repo_name="my-project",
                    gitlab_token="glpat-token",
                    title="Fix bug",
                    head_branch="open-swe/abc123",
                    base_branch="main",
                    body="Description",
                )
            )

        assert url == "https://gitlab.com/my-group/my-project/-/merge_requests/7"
        assert iid == 7
        assert existing is True

    def test_returns_none_on_api_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITLAB_HOST", "gitlab.com")

        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.json.return_value = {"message": "403 Forbidden"}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            url, iid, existing = asyncio.run(
                create_gitlab_mr(
                    repo_owner="my-group",
                    repo_name="my-project",
                    gitlab_token="bad-token",
                    title="Fix bug",
                    head_branch="open-swe/abc123",
                    base_branch="main",
                    body="Description",
                )
            )

        assert url is None
        assert iid is None
        assert existing is False

    def test_uses_correct_project_path_encoding(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify the API URL uses URL-encoded project path (owner%2Frepo)."""
        monkeypatch.setenv("GITLAB_HOST", "git.dtok.io")

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"web_url": "http://x", "iid": 1}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            asyncio.run(
                create_gitlab_mr(
                    repo_owner="dtok-app",
                    repo_name="dtok-core-service",
                    gitlab_token="glpat-token",
                    title="Fix",
                    head_branch="feature",
                    base_branch="main",
                    body="",
                )
            )

        call_url = mock_client.post.call_args[0][0]
        assert "dtok-app%2Fdtok-core-service" in call_url
        assert "git.dtok.io" in call_url


class TestGetGitlabDefaultBranch:
    def test_returns_default_branch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITLAB_HOST", "gitlab.com")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"default_branch": "develop"}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            branch = asyncio.run(
                get_gitlab_default_branch("my-group", "my-project", "glpat-token")
            )

        assert branch == "develop"

    def test_falls_back_to_main_on_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITLAB_HOST", "gitlab.com")

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.json.return_value = {"message": "404 Project Not Found"}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            branch = asyncio.run(
                get_gitlab_default_branch("my-group", "my-project", "glpat-token")
            )

        assert branch == "main"


class TestPostGitlabNote:
    def test_posts_note_successfully(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITLAB_HOST", "gitlab.com")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = asyncio.run(
                post_gitlab_note("my-group", "my-project", "glpat-token", 5, "Hello!")
            )

        assert result is True

    def test_posts_to_issues_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITLAB_HOST", "git.dtok.io")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            asyncio.run(
                post_gitlab_note("dtok-app", "dtok-app", "glpat-token", 10, "Done!")
            )

        call_url = mock_client.post.call_args[0][0]
        assert "/issues/10/notes" in call_url
        assert "git.dtok.io" in call_url

    def test_posts_to_merge_requests(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITLAB_HOST", "gitlab.com")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            asyncio.run(
                post_gitlab_note(
                    "my-group", "my-project", "glpat-token", 3, "MR comment", note_type="merge_requests"
                )
            )

        call_url = mock_client.post.call_args[0][0]
        assert "/merge_requests/3/notes" in call_url


# ---------------------------------------------------------------------------
# resolve_git_token in auth.py
# ---------------------------------------------------------------------------


class TestResolveGitToken:
    def test_gitlab_reads_gitlab_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GIT_PROVIDER", "gitlab")
        monkeypatch.setenv("GITLAB_TOKEN", "glpat-secret")
        monkeypatch.setattr(auth_module, "encrypt_token", lambda t: f"encrypted:{t}")

        config = {"configurable": {"thread_id": "t1", "source": "linear"}}
        token, encrypted = asyncio.run(resolve_git_token(config, "t1"))

        assert token == "glpat-secret"
        assert encrypted == "encrypted:glpat-secret"

    def test_gitlab_raises_if_no_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GIT_PROVIDER", "gitlab")
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)

        config = {"configurable": {"thread_id": "t1", "source": "linear"}}
        with pytest.raises(RuntimeError, match="GITLAB_TOKEN"):
            asyncio.run(resolve_git_token(config, "t1"))

    def test_github_delegates_to_resolve_github_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GIT_PROVIDER", "github")

        called = {}

        async def fake_resolve_github_token(config, thread_id):
            called["config"] = config
            called["thread_id"] = thread_id
            return "gh-token", "encrypted-gh-token"

        monkeypatch.setattr(auth_module, "resolve_github_token", fake_resolve_github_token)

        config = {"configurable": {"thread_id": "t2", "source": "linear"}}
        token, enc = asyncio.run(resolve_git_token(config, "t2"))

        assert token == "gh-token"
        assert called["thread_id"] == "t2"


# ---------------------------------------------------------------------------
# github_comment tool — GitLab dispatch
# ---------------------------------------------------------------------------


_FAKE_REPO_CONFIG = {"configurable": {"repo": {"owner": "my-group", "name": "my-project"}}}
_GC_MODULE = "agent.tools.github_comment"


class TestGithubCommentTool:
    def test_gitlab_dispatch_calls_post_gitlab_note(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GIT_PROVIDER", "gitlab")
        monkeypatch.setenv("GITLAB_TOKEN", "glpat-secret")

        called = {}

        async def fake_post_gitlab_note(owner, name, token, iid, body, *, note_type="issues"):
            called["iid"] = iid
            called["body"] = body
            return True

        with (
            patch(f"{_GC_MODULE}.get_config", return_value=_FAKE_REPO_CONFIG),
            patch(f"{_GC_MODULE}.post_gitlab_note", side_effect=fake_post_gitlab_note),
        ):
            result = github_comment("Hello from agent", 42)

        assert result == {"success": True}
        assert called["iid"] == 42
        assert called["body"] == "Hello from agent"

    def test_gitlab_missing_token_returns_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GIT_PROVIDER", "gitlab")
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)

        with patch(f"{_GC_MODULE}.get_config", return_value=_FAKE_REPO_CONFIG):
            result = github_comment("Hello", 1)

        assert result["success"] is False
        assert "GITLAB_TOKEN" in result["error"]

    def test_missing_issue_number_returns_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GIT_PROVIDER", "gitlab")
        monkeypatch.setenv("GITLAB_TOKEN", "glpat-secret")

        with patch(f"{_GC_MODULE}.get_config", return_value=_FAKE_REPO_CONFIG):
            result = github_comment("Hello", 0)

        assert result["success"] is False

    def test_empty_message_returns_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GIT_PROVIDER", "gitlab")
        monkeypatch.setenv("GITLAB_TOKEN", "glpat-secret")

        with patch(f"{_GC_MODULE}.get_config", return_value=_FAKE_REPO_CONFIG):
            result = github_comment("   ", 5)

        assert result["success"] is False


# ---------------------------------------------------------------------------
# fetch_gitlab_issue_notes
# ---------------------------------------------------------------------------


class TestFetchGitlabIssueNotes:
    def test_returns_notes_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITLAB_HOST", "git.dtok.io")

        notes = [
            {"id": 1, "body": "First comment", "author": {"username": "alice"}, "system": False},
            {"id": 2, "body": "Second comment", "author": {"username": "bob"}, "system": False},
        ]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = notes

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            from agent.utils.gitlab import fetch_gitlab_issue_notes

            result = asyncio.run(
                fetch_gitlab_issue_notes("dtok-app", "dtok-core-service", "glpat-token", 1)
            )

        assert len(result) == 2
        assert result[0]["body"] == "First comment"

    def test_returns_empty_on_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITLAB_HOST", "git.dtok.io")

        mock_response = MagicMock()
        mock_response.status_code = 403

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            from agent.utils.gitlab import fetch_gitlab_issue_notes

            result = asyncio.run(
                fetch_gitlab_issue_notes("owner", "repo", "glpat-token", 5)
            )

        assert result == []


# ---------------------------------------------------------------------------
# GitLab webhook helpers (webapp.py)
# ---------------------------------------------------------------------------


class TestGitlabWebhookHelpers:
    def test_generate_thread_id_from_gitlab_issue_is_deterministic(self) -> None:
        from agent.webapp import generate_thread_id_from_gitlab_issue

        tid1 = generate_thread_id_from_gitlab_issue("12345")
        tid2 = generate_thread_id_from_gitlab_issue("12345")
        assert tid1 == tid2

    def test_generate_thread_id_differs_per_issue(self) -> None:
        from agent.webapp import generate_thread_id_from_gitlab_issue

        tid1 = generate_thread_id_from_gitlab_issue("111")
        tid2 = generate_thread_id_from_gitlab_issue("222")
        assert tid1 != tid2

    def test_verify_gitlab_webhook_token_no_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITLAB_WEBHOOK_SECRET", "")
        from agent.webapp import _verify_gitlab_webhook_token

        assert _verify_gitlab_webhook_token("anything") is True

    def test_verify_gitlab_webhook_token_correct(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITLAB_WEBHOOK_SECRET", "my-secret")
        from agent.webapp import _verify_gitlab_webhook_token

        assert _verify_gitlab_webhook_token("my-secret") is True

    def test_verify_gitlab_webhook_token_wrong(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITLAB_WEBHOOK_SECRET", "my-secret")
        from agent.webapp import _verify_gitlab_webhook_token

        assert _verify_gitlab_webhook_token("wrong-secret") is False


class TestGitlabWebhookEndpoint:
    """Integration-style tests for POST /webhooks/gitlab."""

    def _make_note_payload(
        self,
        note_body: str = "@openswe please fix this",
        noteable_type: str = "Issue",
        path_with_namespace: str = "dtok-app/dtok-core-service",
        issue_id: int = 42,
        issue_iid: int = 7,
    ) -> dict:
        return {
            "object_kind": "note",
            "user": {"id": 1, "name": "Test User", "username": "testuser"},
            "project": {
                "name": "dtok-core-service",
                "namespace": "dtok-app",
                "path_with_namespace": path_with_namespace,
                "web_url": f"https://git.dtok.io/{path_with_namespace}",
            },
            "object_attributes": {
                "id": 100,
                "note": note_body,
                "noteable_type": noteable_type,
                "noteable_id": issue_id,
                "url": f"https://git.dtok.io/{path_with_namespace}/-/issues/{issue_iid}#note_100",
            },
            "issue": {
                "id": issue_id,
                "iid": issue_iid,
                "title": "Test Issue",
                "description": "Issue description",
                "state": "opened",
                "url": f"https://git.dtok.io/{path_with_namespace}/-/issues/{issue_iid}",
            },
        }

    def test_ignores_non_note_events(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from fastapi import BackgroundTasks

        from agent.webapp import gitlab_webhook

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"object_kind": "push"}')
        request.headers = {"X-Gitlab-Token": ""}

        result = asyncio.run(gitlab_webhook(request, BackgroundTasks()))
        assert result["status"] == "ignored"

    def test_ignores_notes_not_on_issues(self) -> None:
        import asyncio
        import json as _json
        from unittest.mock import AsyncMock, MagicMock

        from fastapi import BackgroundTasks

        from agent.webapp import gitlab_webhook

        payload = self._make_note_payload(noteable_type="MergeRequest")
        request = MagicMock()
        request.body = AsyncMock(return_value=_json.dumps(payload).encode())
        request.headers = {"X-Gitlab-Token": ""}

        result = asyncio.run(gitlab_webhook(request, BackgroundTasks()))
        assert result["status"] == "ignored"
        assert "MergeRequest" in result["reason"]

    def test_ignores_notes_without_mention(self) -> None:
        import asyncio
        import json as _json
        from unittest.mock import AsyncMock, MagicMock

        from fastapi import BackgroundTasks

        from agent.webapp import gitlab_webhook

        payload = self._make_note_payload(note_body="Just a regular comment")
        request = MagicMock()
        request.body = AsyncMock(return_value=_json.dumps(payload).encode())
        request.headers = {"X-Gitlab-Token": ""}

        result = asyncio.run(gitlab_webhook(request, BackgroundTasks()))
        assert result["status"] == "ignored"

    def test_accepts_note_with_mention(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import asyncio
        import json as _json
        from unittest.mock import AsyncMock, MagicMock

        from agent.webapp import gitlab_webhook

        monkeypatch.setenv("GITLAB_WEBHOOK_SECRET", "")

        payload = self._make_note_payload(note_body="@openswe please implement this")
        request = MagicMock()
        request.body = AsyncMock(return_value=_json.dumps(payload).encode())
        request.headers = {"X-Gitlab-Token": ""}

        tasks_added = []

        class FakeBGTasks:
            def add_task(self, fn, **kwargs):
                tasks_added.append((fn.__name__, kwargs))

        result = asyncio.run(gitlab_webhook(request, FakeBGTasks()))
        assert result["status"] == "accepted"
        assert len(tasks_added) == 1
        name, kwargs = tasks_added[0]
        assert name == "process_gitlab_issue"
        assert kwargs["repo_owner"] == "dtok-app"
        assert kwargs["repo_name"] == "dtok-core-service"
        assert kwargs["issue_iid"] == 7

    def test_rejects_invalid_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import asyncio
        import json as _json
        from unittest.mock import AsyncMock, MagicMock

        from fastapi import BackgroundTasks, HTTPException

        from agent.webapp import gitlab_webhook

        monkeypatch.setenv("GITLAB_WEBHOOK_SECRET", "correct-secret")

        payload = self._make_note_payload()
        request = MagicMock()
        request.body = AsyncMock(return_value=_json.dumps(payload).encode())
        request.headers = {"X-Gitlab-Token": "wrong-secret"}

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(gitlab_webhook(request, BackgroundTasks()))

        assert exc_info.value.status_code == 401

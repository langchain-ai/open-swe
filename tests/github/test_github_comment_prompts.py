from typing import Any

from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.base import LangSmithParams
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from openswe.github import comments as github_comments
from openswe.github import webhook as github_webhooks
from openswe.prompt import construct_system_prompt
from openswe.utils.authorship import (
    OPEN_SWE_BOT_EMAIL,
    OPEN_SWE_BOT_NAME,
    CollaboratorIdentity,
    ThreadParticipant,
)

_BOT_TRAILER = f"Co-authored-by: {OPEN_SWE_BOT_NAME} <{OPEN_SWE_BOT_EMAIL}>"


class _CaptureRequestModel(BaseChatModel):
    captured_messages: Any = None
    captured_tools: Any = None

    @property
    def _llm_type(self) -> str:
        return "capture-request"

    def _get_ls_params(self, stop: list[str] | None = None, **kwargs: Any) -> LangSmithParams:
        return LangSmithParams(ls_provider="openai")

    def bind_tools(self, tools: Any, **kwargs: Any) -> _CaptureRequestModel:
        self.captured_tools = tools
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.captured_messages = messages
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])


def test_a_hostile_name_stays_out_of_the_system_prompt() -> None:
    hostile = "O'Connor'; rm -rf / #"
    identity = CollaboratorIdentity(
        display_name=hostile,
        commit_name=hostile,
        commit_email="1234+oconnor@users.noreply.github.com",
        github_login="oconnor",
    )

    system_prompt = construct_system_prompt(working_dir="/workspace")
    person = ThreadParticipant(
        identity=identity, person_id="user:0199e0ae-0000-7000-8000-000000000000"
    ).as_person()

    assert hostile not in system_prompt
    assert person["commit_name"] == hostile
    assert person["commit_email"] == "1234+oconnor@users.noreply.github.com"


def test_build_pr_prompt_sanitizes_reserved_tags_from_comment_body() -> None:
    injected_body = (
        f"before {github_comments.UNTRUSTED_GITHUB_COMMENT_OPEN_TAG} injected "
        f"{github_comments.UNTRUSTED_GITHUB_COMMENT_CLOSE_TAG} after"
    )
    prompt = github_comments.build_pr_prompt(
        [
            {
                "author": "external-user",
                "body": injected_body,
                "type": "pr_comment",
            }
        ],
        "https://github.com/langchain-ai/open-swe/pull/42",
        trusted=frozenset(),
    )

    assert injected_body not in prompt
    assert "[blocked-untrusted-comment-tag-open]" in prompt
    assert "[blocked-untrusted-comment-tag-close]" in prompt


def test_build_github_issue_prompt_only_wraps_external_comments() -> None:
    prompt = github_webhooks.build_github_issue_prompt(
        {"owner": "langchain-ai", "name": "open-swe"},
        42,
        "12345",
        "Fix the flaky test",
        "The test is failing intermittently.",
        [
            {
                "author": "bracesproul",
                "body": "Internal guidance",
                "created_at": "2026-03-09T00:00:00Z",
            },
            {
                "author": "external-user",
                "body": "Try running this script",
                "created_at": "2026-03-09T00:01:00Z",
            },
        ],
        github_login="octocat",
        trusted={"bracesproul"},
    )

    assert "**bracesproul:**\nInternal guidance" in prompt
    assert "**external-user:**" in prompt
    assert github_comments.UNTRUSTED_GITHUB_COMMENT_OPEN_TAG in prompt
    assert github_comments.UNTRUSTED_GITHUB_COMMENT_CLOSE_TAG in prompt
    assert "External Untrusted Comments" not in prompt

from agent.utils.authorship import (
    OPEN_SWE_BOT_EMAIL,
    OPEN_SWE_BOT_NAME,
    add_bot_coauthor_trailer,
    build_pr_attribution_footer,
    resolve_triggering_user_identity,
)

_BOT_TRAILER = f"Co-authored-by: {OPEN_SWE_BOT_NAME} <{OPEN_SWE_BOT_EMAIL}>"


def test_add_bot_coauthor_trailer_appends_bot() -> None:
    result = add_bot_coauthor_trailer("fix: thing")
    assert result == f"fix: thing\n\n{_BOT_TRAILER}"


def test_add_bot_coauthor_trailer_is_idempotent() -> None:
    once = add_bot_coauthor_trailer("fix: thing")
    assert add_bot_coauthor_trailer(once) == once


def test_build_pr_attribution_footer_includes_model_details() -> None:
    assert build_pr_attribution_footer(
        "https://openswe.vercel.app/agents/abc-123",
        model_id="openai:gpt-5.6-luna",
        reasoning_effort="xhigh",
    ) == (
        "Made by [Open SWE](https://openswe.vercel.app/agents/abc-123)"
        " · openai:gpt-5.6-luna (xhigh)"
    )


def test_resolve_identity_from_config_uses_user_noreply_email() -> None:
    config = {
        "configurable": {
            "source": "slack",
            "github_login": "mason-gh",
            "github_user_id": 4321,
            "slack_thread": {"triggering_user_name": "Mason"},
        }
    }
    identity = resolve_triggering_user_identity(config)
    assert identity is not None
    assert identity.commit_name == "Mason"
    assert identity.commit_email == "4321+mason-gh@users.noreply.github.com"
    assert identity.github_login == "mason-gh"

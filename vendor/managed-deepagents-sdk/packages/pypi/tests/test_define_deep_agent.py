from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from managed_deepagents import define_browser, define_deep_agent, define_mcp, define_sandbox

if TYPE_CHECKING:
    from managed_deepagents._connectors import McpServersDefinition


def _accounts_mcp() -> McpServersDefinition:
    return define_mcp(
        servers={
            "accounts": {
                "transport": "http",
                "url": "https://example.com/mcp",
            }
        }
    )


@pytest.mark.parametrize(
    ("key", "guidance"),
    [
        ("backend", "Configure the sandbox in sandbox/."),
        ("store", "MDA provides the store automatically."),
        ("checkpointer", "MDA provides the checkpointer automatically."),
        (
            "system_prompt",
            (
                "Use `instructions` in a factory entrypoint or put the static prompt "
                "in instructions.md."
            ),
        ),
        ("state_schema", "Custom state schemas are not supported in Python."),
    ],
)
@pytest.mark.parametrize("value", ["private-config-value", None])
def test_define_deep_agent_explains_rejected_keys(key: str, guidance: str, value: object) -> None:
    with pytest.raises(TypeError) as exc:
        define_deep_agent(name="research", **cast("Any", {key: value}))
    assert str(exc.value) == f"Remove `{key}` from define_deep_agent(...). {guidance}"


def test_define_deep_agent_rejects_unknown_keywords() -> None:
    with pytest.raises(TypeError, match="unexpected keyword argument 'unknown_option'"):
        define_deep_agent(name="research", **cast("Any", {"unknown_option": None}))


def test_define_deep_agent_keeps_valid_config_unchanged() -> None:
    agent = define_deep_agent(name="research", model="openai:gpt-5", tools=[])
    assert agent.kind == "deep-agent"
    assert agent.config == {"name": "research", "model": "openai:gpt-5", "tools": []}


def test_define_deep_agent_distinguishes_omitted_and_none_selections() -> None:
    assert define_deep_agent(name="research").config == {"name": "research"}
    agent = define_deep_agent(
        name="research", instructions=None, mcp=None, sandbox=None, skills=None, memory=None
    )
    assert agent.config == {
        "name": "research",
        "instructions": None,
        "mcp": None,
        "sandbox": None,
        "skills": None,
        "memory": None,
    }
    assert agent.config["memory"] is None


def test_define_deep_agent_selects_a_browser_or_none() -> None:
    browser = define_browser(scope="user")
    assert define_deep_agent(name="research", browser=browser).config["browser"] == browser
    assert define_deep_agent(name="research", browser=None).config["browser"] is None
    with pytest.raises(TypeError, match="browser must be a BrowserDefinition or None"):
        define_deep_agent(name="research", browser=cast("Any", "user"))


def test_define_deep_agent_keeps_concrete_empty_selections() -> None:
    agent = define_deep_agent(name="research", instructions="", mcp=[], skills=[])
    assert agent.config == {
        "name": "research",
        "instructions": "",
        "mcp": (),
        "skills": (),
    }


def test_define_deep_agent_accepts_sandbox_definition() -> None:
    sandbox = define_sandbox(idle_ttl_seconds=600)
    selected = define_deep_agent(name="research", sandbox=sandbox).config["sandbox"]

    assert selected == sandbox
    assert selected is not sandbox


def test_define_deep_agent_snapshots_managed_selections() -> None:
    accounts_mcp = _accounts_mcp()
    mcp = [accounts_mcp]
    files = {"references/checklist.md": "Original"}
    skill = {
        "name": "team-playbook",
        "description": "Team handoffs",
        "markdown": "# Playbook",
        "files": files,
    }
    skills = ["/project/skills/local-skill", skill]
    agent = define_deep_agent(
        name="research",
        mcp=mcp,
        skills=cast("Any", skills),
    )

    mcp.append(
        define_mcp(
            servers={
                "other": {
                    "transport": "http",
                    "url": "https://example.com/other",
                }
            }
        )
    )
    files["references/checklist.md"] = "Changed"
    skill["description"] = "Changed"
    skills.append("other")

    assert agent.config["mcp"] == (accounts_mcp,)
    assert cast("Any", agent.config["mcp"])[0] is accounts_mcp
    assert agent.config["skills"] == (
        "/project/skills/local-skill",
        {
            "name": "team-playbook",
            "description": "Team handoffs",
            "markdown": "# Playbook",
            "files": {"references/checklist.md": "Original"},
        },
    )
    with pytest.raises(TypeError):
        cast("Any", agent.config["skills"])[1]["files"]["references/checklist.md"] = "Changed"


@pytest.mark.parametrize("path", ["./skills/refunds", r".\skills\refunds"])
def test_define_deep_agent_accepts_relative_skill_path(path: str) -> None:
    agent = define_deep_agent(name="support", skills=[path])

    assert agent.config["skills"] == (path,)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("mcp", "accounts", "mcp must be a list"),
        (
            "mcp",
            [_accounts_mcp()] * 2,
            "mcp contains duplicate definitions",
        ),
        ("mcp", ["accounts"], "mcp contains an invalid definition"),
        ("mcp", [{"kind": "mcp_servers"}], "mcp contains an invalid definition"),
        ("sandbox", {}, "sandbox must be a SandboxDefinition or None"),
        (
            "skills",
            ["/project/skills/playbook", r"C:\project\skills\playbook"],
            "skills contains duplicate names",
        ),
        ("skills", ["playbook"], "skills contains an invalid path"),
        ("skills", [Path("/project/skills/playbook")], "skills contains an invalid skill"),
        (
            "skills",
            ["/project/skills/playbook/SKILL.md"],
            "skills contains an invalid path",
        ),
        (
            "skills",
            ["/project/../skills/playbook"],
            "skills contains an invalid path",
        ),
        (
            "skills",
            ["./../skills/playbook"],
            "skills contains an invalid path",
        ),
        (
            "skills",
            [{"name": "playbook", "description": "", "markdown": "x"}],
            "skills contains an invalid generated skill",
        ),
        (
            "skills",
            [{"name": "Invalid_Name", "description": "x", "markdown": "x"}],
            "skills contains an invalid generated skill",
        ),
        (
            "skills",
            [
                {
                    "name": "playbook",
                    "description": "x",
                    "markdown": "x",
                    "files": {"../secret": "x"},
                }
            ],
            "skills files contain an invalid path or content",
        ),
        (
            "skills",
            [
                {
                    "name": "playbook",
                    "description": "x",
                    "markdown": "x",
                    "files": {"references/has space.md": "x"},
                }
            ],
            "skills files contain an invalid path or content",
        ),
    ],
)
def test_define_deep_agent_rejects_invalid_selections(
    field: str, value: object, message: str
) -> None:
    with pytest.raises(TypeError, match=message):
        define_deep_agent(name="research", **cast("Any", {field: value}))


def test_define_deep_agent_requires_non_empty_name() -> None:
    with pytest.raises(TypeError, match="name is required"):
        define_deep_agent(name="", model="openai:gpt-5")


def test_define_deep_agent_rejects_disable_memory() -> None:
    # Both values are rejected, not just ``False``: the old flag's absence used to
    # mean "memory on", so honoring it either way would flip meaning silently.
    for disable_memory in (True, False, "true"):
        with pytest.raises(TypeError, match="disable_memory is no longer supported"):
            define_deep_agent(
                name="research",
                model="openai:gpt-5",
                disable_memory=cast("Any", disable_memory),
            )
    with pytest.raises(TypeError, match=r"memory\.py"):
        define_deep_agent(name="research", model="openai:gpt-5", disable_memory=True)


def test_define_deep_agent_keeps_metadata_on_config() -> None:
    agent = define_deep_agent(
        name="research",
        model="openai:gpt-5",
        metadata={"source_type": "Chat-LangChain", "prompt_source": "hub:demo"},
    )
    assert agent.config["metadata"] == {
        "source_type": "Chat-LangChain",
        "prompt_source": "hub:demo",
    }


def test_define_deep_agent_rejects_non_mapping_metadata() -> None:
    with pytest.raises(TypeError, match="metadata must be a mapping"):
        define_deep_agent(name="research", model="openai:gpt-5", metadata="nope")  # type: ignore[arg-type]

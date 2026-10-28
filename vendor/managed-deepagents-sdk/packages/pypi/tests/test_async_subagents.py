"""Async subagents: specs marked `"async": True` that MDA builds as their own graph."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal

import pytest
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT

from managed_deepagents import define_deep_agent, runtime
from managed_deepagents._async_subagents import (
    GENERAL_PURPOSE,
    SUBAGENT_SKILLS_KEY,
    async_subagent_definition,
    with_async_general_purpose,
)
from managed_deepagents.define_deep_agent import DeepAgentDefinition

if TYPE_CHECKING:
    from managed_deepagents.define_deep_agent import ManagedAsyncSubAgent

RESEARCHER = {
    "name": "researcher",
    "description": "Researches a topic.",
    "system_prompt": "Research the request.",
    "async": True,
}
HELPER = {"name": "helper", "description": "Helps.", "system_prompt": "Help."}
GRAPHS = {"researcher": "lead--researcher"}


def _compile(
    monkeypatch: pytest.MonkeyPatch, config: dict[str, object], **kwargs: Any
) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    class FakeAgent:
        def with_config(self, _config: dict[str, Any]) -> FakeAgent:
            return self

    monkeypatch.setattr(
        runtime, "create_deep_agent", lambda **kw: captured.update(kw) or FakeAgent()
    )
    asyncio.run(
        runtime.compile_managed_agent_async(
            DeepAgentDefinition({"name": "lead", **config}),
            run_context={},
            report_telemetry=False,
            **kwargs,
        )
    )
    return captured


def test_lead_launches_async_subagent_on_its_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _compile(
        monkeypatch, {"subagents": [RESEARCHER, HELPER]}, async_subagent_graphs=GRAPHS
    )

    researcher, helper = captured["subagents"]
    assert researcher == {
        "name": "researcher",
        "description": "Researches a topic.",
        "graph_id": "lead--researcher",
    }
    assert helper["name"] == "helper"


@pytest.mark.parametrize("strategy", ["interrupt", "enqueue"])
def test_async_subagent_keeps_its_update_strategy(
    monkeypatch: pytest.MonkeyPatch, strategy: Literal["interrupt", "enqueue"]
) -> None:
    worker: ManagedAsyncSubAgent = {
        "name": "researcher",
        "description": "Researches a topic.",
        "system_prompt": "Research the request.",
        "async": True,
        "update_strategy": strategy,
    }
    agent = define_deep_agent(name="lead", subagents=[worker])

    captured = _compile(monkeypatch, agent.config, async_subagent_graphs=GRAPHS)

    (researcher,) = captured["subagents"]
    assert researcher["update_strategy"] == strategy


def test_async_subagent_keeps_its_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    hook = "https://app.example.com/webhooks/subagent-finished"
    captured = _compile(
        monkeypatch,
        {"subagents": [{**RESEARCHER, "webhook": hook}]},
        async_subagent_graphs=GRAPHS,
    )

    (researcher,) = captured["subagents"]
    assert researcher["webhook"] == hook


def test_async_subagent_keeps_its_notify_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _compile(
        monkeypatch,
        {"subagents": [{**RESEARCHER, "notify_parent": False}]},
        async_subagent_graphs=GRAPHS,
    )

    (researcher,) = captured["subagents"]
    assert researcher["notify_parent"] is False


@pytest.mark.parametrize("mode", ["isolated", "fork"])
def test_async_subagent_keeps_its_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    captured = _compile(
        monkeypatch, {"subagents": [{**RESEARCHER, "mode": mode}]}, async_subagent_graphs=GRAPHS
    )

    (researcher,) = captured["subagents"]
    assert researcher["mode"] == mode


def test_async_subagent_without_a_graph_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="has no graph"):
        _compile(monkeypatch, {"subagents": [RESEARCHER]}, async_subagent_graphs={})


def test_async_subagent_never_runs_as_a_sync_subagent_in_eval() -> None:
    with pytest.raises(ValueError, match="not supported in `mda eval`"):
        asyncio.run(
            runtime.compile_agent_factory_for_eval_async(
                lambda _runtime: DeepAgentDefinition({"name": "lead", "subagents": [RESEARCHER]})
            )
        )


def test_static_eval_rejects_async_subagents() -> None:
    with pytest.raises(ValueError, match="not supported in `mda eval`"):
        runtime.compile_managed_agent_for_eval(
            DeepAgentDefinition({"name": "lead", "subagents": [RESEARCHER]})
        )


def test_async_subagent_inherits_unset_fields_from_the_lead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lead_tool = SimpleNamespace(name="lead_tool")
    captured = _compile(
        monkeypatch,
        {
            "model": "lead-model",
            "tools": [lead_tool],
            "middleware": [object()],
            "interrupt_on": {"lead_tool": True},
            "subagents": [RESEARCHER, HELPER],
        },
        async_subagent="researcher",
    )

    assert captured["model"] == "lead-model"
    assert [tool.name for tool in captured["tools"]] == ["lead_tool"]
    assert captured["interrupt_on"] == {"lead_tool": True}
    assert "subagents" not in captured
    assert captured["system_prompt"].endswith("Research the request.")
    assert not any(type(entry) is object for entry in captured["middleware"])


def test_async_subagent_does_not_get_the_leads_memory() -> None:
    lead = DeepAgentDefinition({"name": "lead", "memory": object(), "subagents": [RESEARCHER]})

    assert async_subagent_definition(lead, "researcher").config["memory"] is None


def test_async_subagent_loads_only_the_skill_sources_it_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    named = _compile(
        monkeypatch,
        {"subagents": [{**RESEARCHER, "skills": ["/skills/"]}]},
        async_subagent="researcher",
        has_skills=True,
    )
    unnamed = _compile(
        monkeypatch, {"subagents": [RESEARCHER]}, async_subagent="researcher", has_skills=True
    )

    assert named["skills"] == ["/skills/"]
    assert "skills" not in unnamed


def test_async_subagent_names_no_skills_mount_that_isnt_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _compile(
        monkeypatch,
        {"subagents": [{**RESEARCHER, "skills": ["/skills/"]}]},
        async_subagent="researcher",
    )

    assert "skills" not in captured


def test_async_subagent_keeps_the_leads_skills_selection() -> None:
    lead = DeepAgentDefinition({"name": "lead", "skills": ["brief"], "subagents": [RESEARCHER]})

    assert async_subagent_definition(lead, "researcher").config["skills"] == ["brief"]


def test_async_subagent_fields_override_the_lead(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _compile(
        monkeypatch,
        {
            "model": "lead-model",
            "tools": [SimpleNamespace(name="lead_tool")],
            "subagents": [{**RESEARCHER, "model": "own-model", "tools": []}],
        },
        async_subagent="researcher",
    )

    assert captured["model"] == "own-model"
    assert captured["tools"] == []


def test_unknown_async_subagent_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="is not declared"):
        _compile(monkeypatch, {"subagents": [HELPER]}, async_subagent="helper")


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({**RESEARCHER, "async": "yes"}, "must be True or False"),
        ({**RESEARCHER, "graph_id": "other"}, "cannot set `graph_id`: MDA builds"),
        ({**RESEARCHER, "skills": "/skills/"}, "list of skill source paths"),
        ({"name": "r", "system_prompt": "p", "async": True}, "requires a `description`"),
        ({**RESEARCHER, "update_strategy": "invalid"}, 'must be "interrupt" or "enqueue"'),
        ({**RESEARCHER, "update_strategy": None}, 'must be "interrupt" or "enqueue"'),
        ({**RESEARCHER, "update_strategy": []}, 'must be "interrupt" or "enqueue"'),
        ({**RESEARCHER, "webhook": 1}, "must be a URL string"),
        ({**RESEARCHER, "notify_parent": "no"}, "must be True or False"),
        ({**RESEARCHER, "mode": "copy"}, 'must be "isolated" or "fork"'),
    ],
)
def test_define_deep_agent_validates_async_subagents(spec: dict[str, object], message: str) -> None:
    with pytest.raises(TypeError, match=message):
        define_deep_agent(name="lead", subagents=[spec])  # ty: ignore[invalid-argument-type]


# The general-purpose subagent, which served Python agents also get async.

GP_GRAPHS = {"general-purpose": "lead--general-purpose"}


def test_lead_gets_an_async_general_purpose_subagent(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _compile(monkeypatch, {}, async_subagent_graphs=GP_GRAPHS)

    (general_purpose,) = captured["subagents"]
    assert general_purpose["graph_id"] == "lead--general-purpose"
    assert general_purpose["name"] == "general-purpose"
    assert general_purpose["description"]


def test_async_general_purpose_graph_builds_without_a_declaration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _compile(monkeypatch, {"model": "lead-model"}, async_subagent="general-purpose")

    assert captured["model"] == "lead-model"
    assert "subagents" not in captured


def test_async_general_purpose_copies_a_declared_sync_one() -> None:
    sync_gp = {"name": "general-purpose", "description": "Our own.", "system_prompt": "Be ours."}
    lead = DeepAgentDefinition({"name": "lead", "subagents": [sync_gp]})

    config = async_subagent_definition(with_async_general_purpose(lead), "general-purpose").config

    assert config["instructions"] == "Be ours."
    assert config[SUBAGENT_SKILLS_KEY] == []  # only its own skills, as with deepagents


def test_a_declared_general_purpose_keeps_its_own_skills() -> None:
    sync_gp = {"name": "general-purpose", "description": "Ours.", "skills": ["/skills/notes/"]}
    lead = DeepAgentDefinition({"name": "lead", "subagents": [sync_gp]})

    config = async_subagent_definition(with_async_general_purpose(lead), "general-purpose").config

    assert config[SUBAGENT_SKILLS_KEY] == ["/skills/notes/"]


def test_default_async_general_purpose_inherits_the_leads_skills() -> None:
    lead = DeepAgentDefinition({"name": "lead"})

    config = async_subagent_definition(with_async_general_purpose(lead), "general-purpose").config

    assert config["instructions"] == GENERAL_PURPOSE_SUBAGENT["system_prompt"]
    assert config[SUBAGENT_SKILLS_KEY] == ["/skills/"]


def test_a_forked_general_purpose_is_not_copied() -> None:
    forked = {
        "name": "general-purpose",
        "description": "Fork.",
        "system_prompt": "Add.",
        "mode": "fork",
    }
    lead = DeepAgentDefinition({"name": "lead", "subagents": [forked]})

    config = async_subagent_definition(with_async_general_purpose(lead), "general-purpose").config

    assert config["instructions"] == GENERAL_PURPOSE_SUBAGENT["system_prompt"]


def test_general_purpose_name_matches_deepagents() -> None:
    assert GENERAL_PURPOSE_SUBAGENT["name"] == GENERAL_PURPOSE


def test_a_declared_async_general_purpose_is_kept() -> None:
    declared = {**RESEARCHER, "name": "general-purpose"}
    lead = DeepAgentDefinition({"name": "lead", "subagents": [declared]})

    assert with_async_general_purpose(lead) is lead

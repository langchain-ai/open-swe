"""Tests for immutable, per-run selected skill backends."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from deepagents.backends.composite import CompositeBackend
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.store.memory import InMemoryStore
from langgraph_sdk.runtime import ServerRuntime, _ReadRuntime
from pydantic import Field

from managed_deepagents import (
    DeepAgentDefinition,
    GeneratedSkill,
    ManagedRunContext,
    define_deep_agent,
    runtime,
)
from managed_deepagents._memory.context_io import result_value
from managed_deepagents._memory.mounts import MountedPathBackend, managed_context_routes
from managed_deepagents._memory.selected_skills import (
    MAX_SELECTED_SKILL_FILE_BYTES,
    MAX_SELECTED_SKILL_FILES,
    MAX_SELECTED_SKILL_TOTAL_BYTES,
    SelectedSkillsBackend,
    resolve_selected_skills,
)


def _backend() -> SelectedSkillsBackend:
    return SelectedSkillsBackend(
        {
            "/skills/research/SKILL.md": (
                "---\nname: research\ndescription: Find facts\n---\n\nUse sources.\n"
            ),
            "/skills/research/references/checklist.md": "first\nsecond fact\n",
        }
    )


def _entries(result: object) -> list[object]:
    entries = result_value(result, "entries")
    assert isinstance(entries, list)
    return entries


def _matches(result: object) -> list[object]:
    matches = result_value(result, "matches")
    assert isinstance(matches, list)
    return matches


def test_backend_implements_sync_and_async_read_surface() -> None:
    backend = _backend()

    root = _entries(backend.ls("/"))
    assert [(result_value(item, "path"), result_value(item, "is_dir")) for item in root] == [
        ("/research/", True)
    ]
    read = backend.read("/research/references/checklist.md", offset=1, limit=1)
    file_data = result_value(read, "file_data")
    assert result_value(file_data, "content") == "second fact\n"
    assert result_value(read, "total_lines") == 2

    globbed = _matches(backend.glob("**/*.md"))
    assert [result_value(item, "path") for item in globbed] == [
        "/research/SKILL.md",
        "/research/references/checklist.md",
    ]
    assert len(_matches(backend.glob("*.md"))) == 2
    assert len(_matches(backend.glob("/**/*.md"))) == 2
    assert [
        result_value(item, "path") for item in _matches(backend.glob("/*.md", "/research/SKILL.md"))
    ] == ["/research/SKILL.md"]
    grepped = _matches(backend.grep("fact", "/research"))
    assert [(result_value(item, "path"), result_value(item, "line")) for item in grepped] == [
        ("/research/SKILL.md", 3),
        ("/research/references/checklist.md", 2),
    ]
    exact = _matches(backend.grep("Find facts", "/research/SKILL.md", "/*.md"))
    assert [result_value(item, "path") for item in exact] == ["/research/SKILL.md"]
    downloaded, missing = backend.download_files(
        ["/research/references/checklist.md", "/research/missing.md"]
    )
    assert result_value(downloaded, "content") == b"first\nsecond fact\n"
    assert result_value(missing, "error") == "file_not_found"

    async def exercise_async() -> None:
        assert _entries(await backend.als("/"))
        assert result_value(await backend.aread("/research/SKILL.md"), "file_data")
        assert _matches(await backend.aglob("**/*.md"))
        assert _matches(await backend.agrep("fact", "/research"))
        assert await backend.adownload_files(["/research/SKILL.md"])

    asyncio.run(exercise_async())


def test_backend_supports_composite_anchored_and_file_scoped_searches() -> None:
    composite = CompositeBackend(default=SelectedSkillsBackend({}), routes={"/skills/": _backend()})

    assert len(_matches(composite.glob("/**/*.md", "/skills"))) == 2
    assert len(_matches(composite.glob("skills/**/*.md", "/"))) == 2
    exact = _matches(composite.grep("Find facts", "/skills/research/SKILL.md", "/*.md"))
    assert [result_value(item, "path") for item in exact] == ["/skills/research/SKILL.md"]

    nested_composite = CompositeBackend(
        default=SelectedSkillsBackend({}),
        routes={
            "/skills/": SelectedSkillsBackend(
                {
                    "/skills/chosen/top.md": "top",
                    "/skills/chosen/skills/reference.md": "nested",
                }
            )
        },
    )
    nested = _matches(nested_composite.glob("skills/*.md", "/skills/chosen"))
    assert [result_value(item, "path") for item in nested] == ["/skills/chosen/skills/reference.md"]


def test_backend_rejects_mutation() -> None:
    backend = _backend()
    assert result_value(backend.write("/research/new.md", "no"), "error") == "permission_denied"
    assert (
        result_value(backend.edit("/research/SKILL.md", "Use", "Ignore"), "error")
        == "permission_denied"
    )
    assert result_value(backend.delete("/research/SKILL.md"), "error") == "permission_denied"
    assert result_value(backend.upload_files([("/research/new.md", b"no")])[0], "error") == (
        "permission_denied"
    )


@pytest.mark.parametrize(
    "operation",
    [
        lambda backend: backend.ls("/../private"),
        lambda backend: backend.read("/research/../../private"),
        lambda backend: backend.glob("../**"),
        lambda backend: backend.glob("*.md", "/../private"),
        lambda backend: backend.grep("secret", "/../private"),
        lambda backend: backend.grep("secret", "/", "../*.md"),
        lambda backend: backend.download_files(["/research/../../private"]),
    ],
)
def test_backend_rejects_traversal(operation: object) -> None:
    with pytest.raises(ValueError, match=r"invalid skills (path|glob)"):
        operation(_backend())  # type: ignore[operator]


def test_backend_enforces_resource_limits() -> None:
    with pytest.raises(ValueError, match="file limit"):
        SelectedSkillsBackend(
            {f"/skills/test/file-{index}.md": "x" for index in range(MAX_SELECTED_SKILL_FILES + 1)}
        )
    with pytest.raises(ValueError, match="file exceeds the size limit"):
        SelectedSkillsBackend({"/skills/test/SKILL.md": "x" * (MAX_SELECTED_SKILL_FILE_BYTES + 1)})
    with pytest.raises(ValueError, match="total size limit"):
        SelectedSkillsBackend(
            {
                f"/skills/test/file-{index}.md": "x" * MAX_SELECTED_SKILL_FILE_BYTES
                for index in range(
                    MAX_SELECTED_SKILL_TOTAL_BYTES // MAX_SELECTED_SKILL_FILE_BYTES + 1
                )
            }
        )


def _write_skill(root: Path, name: str, files: dict[str, str]) -> None:
    for relative, content in files.items():
        target = root / "skills" / name / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


def test_resolver_mixes_local_and_generated_skills_without_sibling_access(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "static",
        {
            "SKILL.md": "---\nname: static\ndescription: Static skill\n---\n",
            "references/a.md": "static reference",
        },
    )
    _write_skill(
        tmp_path,
        "hidden",
        {"SKILL.md": "---\nname: hidden\ndescription: Hidden skill\n---\n"},
    )

    backend = resolve_selected_skills(
        [
            str(tmp_path / "skills" / "static"),
            {
                "name": "generated",
                "description": 'Generated "per run"',
                "markdown": "Follow this run.",
                "files": {"references/b.md": "generated reference"},
            },
        ],
        local_root=str(tmp_path),
    )

    assert [result_value(item, "path") for item in _entries(backend.ls("/"))] == [
        "/static/",
        "/generated/",
    ]
    generated = backend.download_files(["/generated/SKILL.md"])[0]
    assert b'description: "Generated \\"per run\\""' in result_value(generated, "content")
    assert result_value(backend.download_files(["/static/references/a.md"])[0], "content") == (
        b"static reference"
    )
    assert result_value(backend.download_files(["/hidden/SKILL.md"])[0], "error") == (
        "file_not_found"
    )


def test_resolver_supports_relative_skill_path(tmp_path: Path) -> None:
    _write_skill(tmp_path, "refunds", {"SKILL.md": "# Refunds"})

    backend = resolve_selected_skills(["./skills/refunds"], local_root=str(tmp_path))

    selected = backend.download_files(["/refunds/SKILL.md"])[0]
    assert result_value(selected, "content") == b"# Refunds"


def test_resolver_supports_empty_and_concurrent_generated_selections() -> None:
    empty = resolve_selected_skills([])
    assert empty.file_count == 0
    assert _entries(empty.ls("/")) == []

    first, second = [
        resolve_selected_skills(
            [{"name": "same-name", "description": "Run local", "markdown": markdown}]
        )
        for markdown in ("first run", "second run")
    ]
    first_content = result_value(first.download_files(["/same-name/SKILL.md"])[0], "content")
    second_content = result_value(second.download_files(["/same-name/SKILL.md"])[0], "content")
    assert b"first run" in first_content
    assert b"second run" in second_content


def test_explicit_empty_backend_replaces_discovered_skills() -> None:
    context = _ContextBackend()
    empty = SelectedSkillsBackend({})
    selected_routes = managed_context_routes(context, selected_skills_backend=empty)  # type: ignore[arg-type]
    discovered_routes = managed_context_routes(context)  # type: ignore[arg-type]

    assert selected_routes["/skills/"] is empty
    assert isinstance(discovered_routes["/skills/"], MountedPathBackend)


class _ContextBackend:
    def ls(self, path: str) -> object:
        if path == "/skills":
            return SimpleNamespace(entries=[SimpleNamespace(path="/skills/context/", is_dir=True)])
        if path == "/skills/context":
            return SimpleNamespace(
                entries=[SimpleNamespace(path="/skills/context/SKILL.md", is_dir=False, size=8)]
            )
        return SimpleNamespace(entries=[])

    def download_files(self, paths: list[str]) -> list[object]:
        return [SimpleNamespace(path=path, content=b"context skill", error=None) for path in paths]


def test_resolver_copies_a_context_skill() -> None:
    backend = resolve_selected_skills(
        ["/workspace/skills/context"],
        context_backend=_ContextBackend(),  # type: ignore[arg-type]
    )
    assert result_value(backend.download_files(["/context/SKILL.md"])[0], "content") == (
        b"context skill"
    )


@pytest.mark.parametrize(
    ("selection", "error"),
    [
        (["/workspace/skills/missing"], "does not reference"),
        (["static"], "invalid path"),
        (["/workspace/skills/static", r"C:\workspace\skills\static"], "duplicate names"),
        (
            [{"name": "static", "description": "collision", "markdown": "content"}],
            "conflicts",
        ),
        (
            [
                {
                    "name": "unsafe",
                    "description": "bad path",
                    "markdown": "content",
                    "files": {"../secret.md": "secret"},
                }
            ],
            "invalid path or content",
        ),
    ],
)
def test_resolver_rejects_invalid_selections(tmp_path: Path, selection: object, error: str) -> None:
    _write_skill(
        tmp_path,
        "static",
        {"SKILL.md": "---\nname: static\ndescription: Static skill\n---\n"},
    )
    with pytest.raises((TypeError, ValueError), match=error):
        resolve_selected_skills(selection, local_root=str(tmp_path))


def test_resolver_requires_skill_markdown(tmp_path: Path) -> None:
    _write_skill(tmp_path, "incomplete", {"reference.md": "content"})
    with pytest.raises(ValueError, match=r"missing SKILL\.md"):
        resolve_selected_skills([str(tmp_path / "skills" / "incomplete")], local_root=str(tmp_path))


def test_resolver_rejects_symlinks_that_escape_skills_directory(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "static",
        {"SKILL.md": "---\nname: static\ndescription: Static skill\n---\n"},
    )
    outside = tmp_path / "private.md"
    outside.write_text("private")
    (tmp_path / "skills" / "static" / "private.md").symlink_to(outside)

    with pytest.raises(ValueError, match="escapes the skills directory"):
        resolve_selected_skills([str(tmp_path / "skills" / "static")], local_root=str(tmp_path))


class _Model(BaseChatModel):
    prompts: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "selected-skills-test"

    def bind_tools(self, tools: Any, **kwargs: Any) -> _Model:
        return self

    def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        self.prompts.append(str(messages[0].content))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])


def test_real_graph_loads_generated_skill_metadata() -> None:
    model = _Model()
    graph = runtime.compile_managed_agent(
        DeepAgentDefinition(
            {
                "model": model,
                "skills": [
                    {
                        "name": "account-handoff",
                        "description": "Prepare a concise account handoff.",
                        "markdown": "Include the account name and next action.",
                    }
                ],
            }
        ),
        {"context": {}},
        run_context={},
        report_telemetry=False,
    )

    graph.invoke({"messages": [{"role": "user", "content": "Prepare a handoff"}]})

    assert len(model.prompts) == 1
    assert "account-handoff" in model.prompts[0]
    assert "Prepare a concise account handoff." in model.prompts[0]
    assert "/skills/account-handoff/SKILL.md" in model.prompts[0]


def test_eval_graph_loads_selected_static_skill(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "trial-playbook",
        {"SKILL.md": ("---\nname: trial-playbook\ndescription: Follow the trial playbook.\n---\n")},
    )
    model = _Model()
    graph = runtime.compile_managed_agent_for_eval(
        DeepAgentDefinition(
            {"model": model, "skills": [str(tmp_path / "skills" / "trial-playbook")]}
        ),
        {"context": {}, "configurable": {"cwd": str(tmp_path)}},
    )

    graph.invoke({"messages": [{"role": "user", "content": "Prepare a handoff"}]})

    assert len(model.prompts) == 1
    assert "trial-playbook" in model.prompts[0]
    assert "Follow the trial playbook." in model.prompts[0]


@pytest.mark.parametrize(
    "skills",
    [
        ["skills/customer-support"],
        [{"name": "generated", "description": "Generated", "markdown": "# Generated"}],
        [],
    ],
)
def test_factory_skill_inspection_skips_resource_reads(
    monkeypatch: pytest.MonkeyPatch,
    skills: list[str | GeneratedSkill],
) -> None:
    model = _Model()

    def build(_runtime: ServerRuntime[ManagedRunContext]) -> DeepAgentDefinition:
        return define_deep_agent(name="skill-inspection", model=model, skills=skills)

    def forbidden(*args: object, **kwargs: object) -> object:
        pytest.fail("inspection must not resolve skill resources")

    with monkeypatch.context() as patch:
        patch.setattr(runtime, "_open_context_backend", forbidden)
        patch.setattr(runtime, "resolve_selected_skills", forbidden)
        inspected = asyncio.run(
            runtime.compile_managed_agent_async(
                build,
                context_repo="deployed-agent",
                has_skills=True,
                report_telemetry=False,
                runtime=_ReadRuntime(access_context="assistants.read", store=InMemoryStore()),
            )
        )

    reference = runtime.compile_managed_agent(
        define_deep_agent(
            name="skill-inspection",
            model=model,
            skills=[{"name": "reference", "description": "Reference", "markdown": "# Reference"}]
            if skills
            else [],
        ),
        run_context={},
        report_telemetry=False,
    )
    assert list(inspected.get_graph().nodes) == list(reference.get_graph().nodes)
    assert inspected.get_input_jsonschema() == reference.get_input_jsonschema()
    assert inspected.get_output_jsonschema() == reference.get_output_jsonschema()

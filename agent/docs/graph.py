"""Dedicated docs graph with two skills, two checkouts and gated server tools."""

import asyncio
import base64
import json
import posixpath
import shlex
from pathlib import Path
from urllib.parse import urlsplit

from deepagents import create_deep_agent
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.messages import AnyMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import RunnableConfig
from langgraph.pregel import Pregel

from agent.config import ENV
from agent.dashboard.workspace_settings_cache import cached_workspace_settings
from agent.docs import github
from agent.docs.coordinator import complete_check, publication_guard
from agent.docs.models import JOBS, DocsJob
from agent.docs.publish import Finding, finish, publish
from agent.github.comments import fence_github_comment_body
from agent.mcp.models import MCPConnection, MCPConnectionUpdate
from agent.mcp.runtime import MCPSource, discover_tools, load_mcp_tools
from agent.middleware import SanitizeOpenAIResponsesMiddleware, ToolErrorMiddleware
from agent.prompts import apply_tool_descriptions, prompt
from agent.run_config import RunConfig
from agent.runtime import (
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_RECURSION_LIMIT,
    bindable_config,
    ensure_sandbox_for_thread,
    graph_loaded_for_execution,
)
from agent.sandboxes.paths import resolve_sandbox_work_dir
from agent.sandboxes.repo_prep import materialize_trusted_skills, prepare_review_repo
from agent.store import now_iso
from agent.utils.model import make_model, provider_model_kwargs
from agent.utils.url_safety import resolve_and_validate


async def docs_mcp_tools(job: DocsJob) -> list[StructuredTool]:
    url = job.snapshot.settings.docs_mcp_url
    if not url:
        return []
    namespace = ("docs", job.snapshot.settings.revision)
    connection = MCPConnection(
        name="docs", url=url, revision=job.snapshot.settings.revision, updated_at=now_iso()
    )
    definitions = await discover_tools(connection, namespace)
    connection.allowed_tools = [
        definition.name
        for definition in definitions
        if (definition.annotations is not None and definition.annotations.readOnlyHint is True)
        or (
            definition.name.lower().startswith(("search", "fetch", "read", "get", "list"))
            and (
                definition.annotations is None or definition.annotations.destructiveHint is not True
            )
        )
    ]
    if not connection.allowed_tools:
        raise ValueError("Docs MCP exposes no read-only documentation tools")

    async def list_connections() -> list[MCPConnection]:
        return [connection]

    async def get_connection(name: str) -> MCPConnection | None:
        return connection if name == "docs" else None

    async def authorize() -> None:
        await publication_guard(job.snapshot)

    tools = await load_mcp_tools(
        MCPSource(namespace, list_connections, get_connection, authorize), connection_name="docs"
    )
    return [tool for tool in tools if isinstance(tool, StructuredTool)]


async def trusted_guidance(backend: SandboxBackendProtocol, root: str, ref: str) -> str:
    # Read every docs AGENTS.md from the trusted base, including instructions for new paths.
    script = """import subprocess,sys,json
root,ref=sys.argv[1:]
def git(*args): return subprocess.check_output(['git','-C',root,*args])
paths=[p.decode() for p in git('ls-tree','-rz','--name-only',ref).split(b'\\0') if p and p.split(b'/')[-1]==b'AGENTS.md']
sections=[]
size=0
for path in sorted(paths):
    text=git('show',ref+':'+path).decode('utf-8')
    size+=len(text.encode())
    if size>512*1024: raise ValueError('Docs guidance exceeds context limit')
    sections.append('Instructions for '+path+'\\n'+text)
print(json.dumps('\\n\\n'.join(sections)))"""
    result = await backend.aexecute(
        "python3 -c " + shlex.quote(script) + " " + shlex.quote(root) + " " + shlex.quote(ref)
    )
    if result.exit_code != 0 or result.truncated:
        raise ValueError("Could not load all docs AGENTS.md instructions")
    guidance = json.loads(result.output)
    if not isinstance(guidance, str):
        raise ValueError("Invalid docs guidance")
    return guidance


async def screenshot(backend: SandboxBackendProtocol, url: str) -> list[dict[str, object]]:
    url = MCPConnectionUpdate(name="screenshot", url=url).url
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("Screenshots require a public HTTPS URL without credentials")
    safe, _, _, _ = await asyncio.to_thread(resolve_and_validate, url)
    if not safe:
        raise ValueError("Screenshot URL must resolve to public addresses")
    path = "/tmp/open-swe-docs-screenshot.png"
    script = Path(__file__).with_name("browser.py").read_text()
    command = "python3 -c " + shlex.quote(script) + " " + shlex.quote(url) + " " + shlex.quote(path)
    result = await backend.aexecute(command, timeout=60)
    if result.exit_code != 0:
        raise ValueError("Screenshot failed: " + result.output[-1000:])
    files = await backend.adownload_files([path])
    if len(files) != 1 or files[0].content is None or files[0].error:
        raise ValueError("Could not read screenshot")
    if len(files[0].content) > 2 * 1024 * 1024:
        raise ValueError("Screenshot exceeds 2 MiB")
    return [
        {
            "type": "image_url",
            "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(files[0].content).decode()
            },
        }
    ]


async def run_docs(state: MessagesState, config: RunnableConfig) -> dict[str, list[AnyMessage]]:
    cfg = RunConfig.from_config(config)
    job = await JOBS.get(cfg.docs_job_key or "")
    if not job or job.snapshot.fingerprint != cfg.docs_fingerprint:
        return {"messages": []}
    snapshot = job.snapshot
    try:
        # No sandbox or model until authoritative API state passes the deterministic gate.
        await publication_guard(snapshot)
        if ENV.SANDBOX_TYPE.get() != "langsmith":
            raise ValueError(
                "Open SWE Docs requires a managed sandbox with repository-scoped proxy credentials"
            )
        repositories = [snapshot.source_repository, snapshot.settings.docs_repository]
        # Confirm this is an App token, rather than an unrestricted local OAuth fallback.
        for repository in repositories:
            await github.token(repository)
        backend = await ensure_sandbox_for_thread(
            job.thread_id,
            github_proxy_repositories=repositories,
            github_proxy_permissions=github.READ_PERMISSIONS,
            workspace_slug=cfg.workspace_slug,
        )
        work = posixpath.join(await resolve_sandbox_work_dir(backend), "open-swe-docs")
        source_work, docs_work = posixpath.join(work, "source"), posixpath.join(work, "docs")
        paths = [
            source_work,
            docs_work,
            *[posixpath.join(work, "linked", str(link.number)) for link in snapshot.links],
        ]
        result = await backend.aexecute("mkdir -p " + " ".join(map(shlex.quote, paths)))
        if result.exit_code != 0:
            raise ValueError("Could not prepare docs sandbox directories")
        source_owner, source_name = snapshot.source_repository.split("/")
        docs_owner, docs_name = snapshot.settings.docs_repository.split("/")
        if not await prepare_review_repo(
            backend,
            work_dir=source_work,
            repo_owner=source_owner,
            repo_name=source_name,
            head_sha=snapshot.source.head.sha,
            pr_number=snapshot.source.number,
            base_sha=snapshot.source.base.sha,
        ):
            raise ValueError("Could not check out the source PR at the reviewed SHA")
        docs_root = posixpath.join(docs_work, docs_name)
        base_ready = False
        if not snapshot.links:
            # A retry on the same input keeps unpublished authoring work in this thread's sandbox.
            head = await backend.aexecute("git -C " + shlex.quote(docs_root) + " rev-parse HEAD")
            base_ready = head.exit_code == 0 and head.output.strip() == snapshot.docs_base_sha
        if not base_ready and not await prepare_review_repo(
            backend,
            work_dir=docs_work,
            repo_owner=docs_owner,
            repo_name=docs_name,
            head_sha=snapshot.docs_base_sha,
        ):
            raise ValueError("Could not check out the docs base branch")
        linked_paths: list[str] = []
        for link in snapshot.links:
            linked_work = posixpath.join(work, "linked", str(link.number))
            if not await prepare_review_repo(
                backend,
                work_dir=linked_work,
                repo_owner=docs_owner,
                repo_name=docs_name,
                head_sha=link.sha,
                pr_number=link.number,
                base_sha=link.base_sha,
            ):
                raise ValueError("Could not check out a linked docs PR")
            linked_paths.append(
                f"PR #{link.number} ({link.state}, draft={link.draft}): {posixpath.join(linked_work, docs_name)}; base={link.base_sha}; head={link.sha}; URL={link.url}"
            )
        guidance = await trusted_guidance(backend, docs_root, snapshot.docs_base_sha)
        skills_root = posixpath.join(work, "skills")
        uploads = [
            (
                posixpath.join(skills_root, skill, "SKILL.md"),
                Path(__file__)
                .resolve()
                .parent.parent.joinpath("skills", skill, "SKILL.md")
                .read_bytes(),
            )
            for skill in ("docs-review", "docs-author")
        ]
        uploaded = await backend.aupload_files(uploads)
        if any(item.error for item in uploaded) or len(uploaded) != len(uploads):
            raise ValueError("Could not load docs skills")
        skill_sources = [
            skills_root + "/",
            *await materialize_trusted_skills(
                backend, repo_dir=docs_root, trusted_ref=snapshot.docs_base_sha
            ),
        ]

        async def finish_docs_review(
            summary: str, findings: list[Finding], docs_needed: bool = False
        ) -> str:
            return await finish(snapshot, summary, findings, docs_needed)

        async def publish_docs_pr(title: str, body: str) -> str:
            return await publish(snapshot, backend, docs_root, title, body)

        async def take_docs_screenshot(url: str) -> list[dict[str, object]]:
            return await screenshot(backend, url)

        tools = apply_tool_descriptions([finish_docs_review, publish_docs_pr, take_docs_screenshot])
        tools.extend(await docs_mcp_tools(job))
        workspace_settings = await cached_workspace_settings(cfg.workspace_slug)
        (model_id, effort), _ = workspace_settings.default_model_pair("agent")
        model = make_model(
            model_id,
            use_gateway=workspace_settings.effective_gateway_enabled,
            **provider_model_kwargs(model_id, effort, max_tokens=DEFAULT_LLM_MAX_TOKENS),
        )
        await publication_guard(snapshot)
        agent = create_deep_agent(
            model=model,
            backend=backend,
            skills=skill_sources,
            system_prompt=prompt(
                "docs/main",
                source_root=posixpath.join(source_work, source_name),
                source_base=snapshot.source.base.sha,
                source_head=snapshot.source.head.sha,
                docs_root=docs_root,
                skills_root=skills_root,
                linked_paths="\n".join(linked_paths) or "None",
                guidance=guidance,
                context=fence_github_comment_body(
                    snapshot.source.title + "\n\n" + (snapshot.source.body or ""), registered=False
                ),
                mcp_enabled=bool(snapshot.settings.docs_mcp_url),
            ),
            tools=tools,
            middleware=[
                ModelCallLimitMiddleware(run_limit=80, exit_behavior="end"),
                ToolErrorMiddleware(),
                SanitizeOpenAIResponsesMiddleware(),
            ],
        )
        response = await agent.ainvoke(state, config)
        return {"messages": response["messages"]}
    finally:
        current = await JOBS.get(snapshot.key)
        if (
            current
            and current.snapshot.fingerprint == snapshot.fingerprint
            and current.status in {"running", "pending"}
        ):
            current.status = "failed"
            current.result = "Docs agent ended without a completed review or documentation PR."
            await JOBS.put(snapshot.key, current)
            await complete_check(current, "Docs run incomplete", current.result)


async def get_docs(config: RunnableConfig) -> Pregel:
    config["recursion_limit"] = DEFAULT_RECURSION_LIMIT
    graph = StateGraph(MessagesState)
    if graph_loaded_for_execution(config):
        graph.add_node("docs", run_docs)
    else:

        async def idle(_state: MessagesState) -> dict[str, object]:
            return {}

        graph.add_node("docs", idle)
    graph.add_edge(START, "docs")
    graph.add_edge("docs", END)
    return graph.compile().with_config(bindable_config(config))


traced_docs = get_docs

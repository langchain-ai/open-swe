from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "agent" / "server.py"


def load_server_module():
    fake_client = types.SimpleNamespace(threads=types.SimpleNamespace(update=None))

    agent_pkg = types.ModuleType("agent")
    agent_pkg.__path__ = [str(REPO_ROOT / "agent")]

    langgraph_config = types.ModuleType("langgraph.config")
    langgraph_config.get_config = lambda: {"metadata": {}}

    langgraph_state = types.ModuleType("langgraph.graph.state")
    langgraph_state.RunnableConfig = dict

    langgraph_pregel = types.ModuleType("langgraph.pregel")
    langgraph_pregel.Pregel = object

    langgraph_sdk = types.ModuleType("langgraph_sdk")
    langgraph_sdk.get_client = lambda: fake_client

    deepagents = types.ModuleType("deepagents")
    deepagents.create_deep_agent = lambda **kwargs: types.SimpleNamespace(
        with_config=lambda config: {"agent_kwargs": kwargs, "config": config}
    )

    deepagents_protocol = types.ModuleType("deepagents.backends.protocol")
    deepagents_protocol.SandboxBackendProtocol = object

    langsmith_sandbox = types.ModuleType("langsmith.sandbox")
    langsmith_sandbox.SandboxClientError = RuntimeError

    middleware = types.ModuleType("agent.middleware")
    middleware.ToolErrorMiddleware = type("ToolErrorMiddleware", (), {})
    middleware.check_message_queue_before_model = object()
    middleware.ensure_no_empty_msg = object()
    middleware.open_pr_if_needed = object()

    prompt = types.ModuleType("agent.prompt")
    prompt.construct_system_prompt = lambda *args, **kwargs: ""

    tools = types.ModuleType("agent.tools")
    for name in [
        "commit_and_open_pr",
        "create_pr_review",
        "dismiss_pr_review",
        "fetch_url",
        "get_pr_review",
        "github_comment",
        "http_request",
        "linear_comment",
        "linear_create_issue",
        "linear_delete_issue",
        "linear_get_issue",
        "linear_get_issue_comments",
        "linear_list_teams",
        "linear_update_issue",
        "list_pr_review_comments",
        "list_pr_reviews",
        "slack_thread_reply",
        "submit_pr_review",
        "update_pr_review",
        "web_search",
    ]:
        setattr(tools, name, object())

    auth = types.ModuleType("agent.utils.auth")
    auth.resolve_github_token = None

    model = types.ModuleType("agent.utils.model")
    model.make_model = lambda *args, **kwargs: object()

    sandbox = types.ModuleType("agent.utils.sandbox")
    sandbox.create_sandbox = None

    agents_md = types.ModuleType("agent.utils.agents_md")
    agents_md.read_agents_md_in_sandbox = None

    github_utils = types.ModuleType("agent.utils.github")
    github_utils._CRED_FILE_PATH = "/tmp/git-cred"
    for name in [
        "cleanup_git_credentials",
        "git_current_branch",
        "git_has_uncommitted_changes",
        "git_pull_branch",
        "is_valid_git_repo",
        "remove_directory",
        "setup_git_credentials",
    ]:
        setattr(github_utils, name, lambda *args, **kwargs: None)

    sandbox_paths = types.ModuleType("agent.utils.sandbox_paths")
    sandbox_paths.aresolve_repo_dir = None
    sandbox_paths.aresolve_sandbox_work_dir = None

    sandbox_state = types.ModuleType("agent.utils.sandbox_state")
    sandbox_state.SANDBOX_BACKENDS = {}
    sandbox_state.get_sandbox_id_from_metadata = None

    modules = {
        "agent": agent_pkg,
        "langgraph.config": langgraph_config,
        "langgraph.graph.state": langgraph_state,
        "langgraph.pregel": langgraph_pregel,
        "langgraph_sdk": langgraph_sdk,
        "deepagents": deepagents,
        "deepagents.backends.protocol": deepagents_protocol,
        "langsmith.sandbox": langsmith_sandbox,
        "agent.middleware": middleware,
        "agent.prompt": prompt,
        "agent.tools": tools,
        "agent.utils.auth": auth,
        "agent.utils.model": model,
        "agent.utils.sandbox": sandbox,
        "agent.utils.agents_md": agents_md,
        "agent.utils.github": github_utils,
        "agent.utils.sandbox_paths": sandbox_paths,
        "agent.utils.sandbox_state": sandbox_state,
    }

    previous = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    sys.modules.pop("agent.server_under_test", None)

    spec = importlib.util.spec_from_file_location("agent.server_under_test", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module

    try:
        spec.loader.exec_module(module)
        return module, fake_client
    finally:
        sys.modules.pop(spec.name, None)
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


class StaleSandboxCreatingTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_agent_resets_stale_sandbox_creating_without_waiting(self) -> None:
        module, fake_client = load_server_module()
        updates: list[tuple[str, dict[str, object]]] = []
        fake_backend = types.SimpleNamespace(id="sandbox-123")

        async def update(*, thread_id: str, metadata: dict[str, object]) -> None:
            updates.append((thread_id, metadata))

        async def resolve_github_token(config, thread_id):
            return None, ""

        async def clone_repo(_backend, _owner, _repo, github_token=None):
            return "/tmp/open-swe"

        async def read_agents_md(_backend, _repo_dir):
            return ""

        async def get_sandbox_id(_thread_id: str):
            return module.SANDBOX_CREATING

        async def fail_wait(_thread_id: str):
            raise AssertionError("stale SANDBOX_CREATING should reset instead of waiting")

        async def to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)

        fake_client.threads.update = update
        module.resolve_github_token = resolve_github_token
        module._clone_or_pull_repo_in_sandbox = clone_repo
        module.read_agents_md_in_sandbox = read_agents_md
        module.get_sandbox_id_from_metadata = get_sandbox_id
        module._wait_for_sandbox_id = fail_wait
        module.asyncio.to_thread = to_thread
        module.create_sandbox = lambda sandbox_id=None: fake_backend
        module.get_config = lambda: {"metadata": {}}
        module.SANDBOX_BACKENDS.clear()

        config = {
            "configurable": {
                "thread_id": "thread-123",
                "__is_for_execution__": True,
                "repo": {"owner": "langchain-ai", "name": "open-swe"},
            },
            "metadata": {},
        }

        result = await module.get_agent(config)

        self.assertEqual(
            updates,
            [
                ("thread-123", {"sandbox_id": None}),
                ("thread-123", {"sandbox_id": module.SANDBOX_CREATING}),
                ("thread-123", {"repo_dir": "/tmp/open-swe"}),
            ],
        )
        self.assertIs(module.SANDBOX_BACKENDS["thread-123"], fake_backend)
        self.assertIs(result["agent_kwargs"]["backend"], fake_backend)


if __name__ == "__main__":
    unittest.main()

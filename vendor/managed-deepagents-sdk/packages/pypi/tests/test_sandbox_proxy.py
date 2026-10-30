from __future__ import annotations

import asyncio
import base64
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from langsmith.sandbox import ResourceNotFoundError

import managed_deepagents as mda
from managed_deepagents import _sandbox_proxy, runtime
from managed_deepagents._agent_auth import AgentAuthContext
from managed_deepagents._connections import _Connections
from managed_deepagents._sandbox_proxy import (
    patch_sandbox_proxy_config,
    sandbox_credential_consumers,
)
from managed_deepagents.sandbox import ConnectionHeader, SandboxDefinition


def _config() -> dict[str, Any]:
    return {
        "configurable": {
            "thread_id": "t",
            "langgraph_auth_user": {
                "identity": "alice",
                "mda_user_id": "alice",
                "mda_user_kind": "person",
            },
        }
    }


def _template(ref: object) -> dict[str, Any]:
    return {
        "no_proxy": ["localhost"],
        "access_control": {"allow_list": ["api.github.com"]},
        "rules": [
            {
                "name": "api",
                "match_hosts": ["api.github.com"],
                "headers": [
                    {"name": "Raw", "value": ref},
                    {"name": "Bearer", "value": mda.bearer(ref)},
                    {"name": "Basic", "value": mda.basic("üser", ref)},
                    {"name": "Static", "value": "yes"},
                ],
            },
            {"name": "dynamic", "headers": [{"name": "X", "value": ref}]},
            {
                "name": "provider",
                "type": "gcp",
                "gcp": {
                    "service_account_json": {
                        "type": "workspace_secret",
                        "value": "{gcp-account}",
                    }
                },
            },
        ],
    }


def test_header_helpers_preserve_references_and_reject_invalid_inputs() -> None:
    ref = mda.connections.get("github", {"type": "user"})
    config = _template(ref)
    definition = mda.define_sandbox(proxy_config=config)
    headers = definition.options["proxy_config"]["rules"][0]["headers"]
    for original, normalized in zip(config["rules"][0]["headers"], headers, strict=True):
        assert normalized["value"] is original["value"]
    for value in [ref, mda.bearer(ref), mda.basic("user", ref)]:
        header = {"name": "X", "value": value, "type": "opaque"}
        with pytest.raises(TypeError, match="type"):
            mda.define_sandbox(proxy_config={"rules": [{"headers": [header]}]})
    for username in ["", "  ", "u:ser", None, 12]:
        with pytest.raises((ValueError, TypeError)):
            mda.basic(username, ref)
    for helper in [mda.bearer, lambda value: mda.basic("user", value)]:
        with pytest.raises(TypeError):
            helper("secret")
    with pytest.raises(ValueError, match="format"):
        ConnectionHeader(ref, "unknown")
    explicit = mda.connections.get("github", {"type": "user", "id": "another-user"})
    headers = [{"name": "X", "value": ref}, {"name": "Y", "value": explicit}]
    definition = mda.define_sandbox(proxy_config={"rules": [{"headers": headers}]})
    consumer = sandbox_credential_consumers(definition)[0]
    assert consumer.credentials == (ref, explicit)
    assert consumer.load({}, AgentAuthContext("https://auth.example", "alice")) == []


class Store:
    def __init__(self) -> None:
        self.calls = []
        self.token = "fake-secret"
        self.failure = None

    async def fetch(self, request: Any) -> str:
        self.calls.append(request)
        if self.failure:
            raise self.failure
        return self.token


@pytest.fixture
def proxy(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    runtime._managed_sandboxes.clear()
    monkeypatch.setenv("MDA_DEPLOYMENT_NAME", "proxy-test")
    store = Store()
    ref = _Connections(store).get("github", {"type": "user"})
    handle = SimpleNamespace(name="existing-box", id="box-id", status="running")
    client = MagicMock()
    client.get_sandbox.return_value = client.create_sandbox.return_value = handle
    monkeypatch.setattr(runtime, "_sandbox_client_for", lambda _: client)
    box = MagicMock(id="backend-id")
    box.execute.return_value = "done"
    box.aexecute = AsyncMock(return_value="done")
    monkeypatch.setattr(runtime, "LangSmithSandbox", lambda **_: box)
    patch = MagicMock()
    monkeypatch.setattr(_sandbox_proxy, "patch_sandbox_proxy_config", patch)
    state = SimpleNamespace(
        store=store,
        definition=mda.define_sandbox(proxy_config=_template(ref)),
        client=client,
        box=box,
        patch=patch,
    )

    def factory() -> Any:
        return runtime._managed_sandbox_backend(state.definition, _config())

    state.factory = factory
    yield state
    runtime._managed_sandboxes.clear()


@pytest.mark.parametrize("adopt", [False, True])
def test_create_or_adopt_resolves_each_command_and_only_patches_changes(
    proxy: SimpleNamespace,
    adopt: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="mda")
    if not adopt:
        proxy.client.get_sandbox.return_value = None
    root = proxy.factory()
    root.read("/workspace/file")
    assert proxy.store.calls == []
    proxy.patch.assert_not_called()
    static = runtime._sandbox_create_kwargs(proxy.definition)["proxy_config"]
    assert len(static["rules"]) == 2
    assert static["rules"][0]["headers"] == [{"name": "Static", "value": "yes"}]
    if not adopt:
        assert proxy.client.create_sandbox.call_args.kwargs["proxy_config"] == static
    names = runtime._managed_sandbox_lookup_names("proxy-test", "thread:t", proxy.definition)

    def execute(*args: object, **kwargs: object) -> str:
        assert proxy.patch.call_count > 0  # PATCH must precede the first command.
        return "done"

    proxy.box.execute.side_effect = execute
    assert root.execute("one") == "done"
    wire = proxy.patch.call_args.args[2]
    original = proxy.definition.options["proxy_config"]
    expected_headers = [
        {"name": "Raw", "value": "fake-secret", "type": "opaque"},
        {"name": "Bearer", "value": "Bearer fake-secret", "type": "opaque"},
        {
            "name": "Basic",
            "value": "Basic " + base64.b64encode("üser:fake-secret".encode()).decode(),
            "type": "opaque",
        },
        {"name": "Static", "value": "yes"},
    ]
    assert wire == {
        **original,
        "rules": [
            {**original["rules"][0], "headers": expected_headers},
            {**original["rules"][1], "headers": [{**expected_headers[0], "name": "X"}]},
            original["rules"][2],
        ],
    }
    assert proxy.patch.call_args.args[1] == "existing-box"
    root.bind_runtime(_config()).execute("two")
    assert len(proxy.store.calls) == 2  # Duplicate header references resolve once per command.
    assert proxy.patch.call_count == 1
    proxy.store.token = "rotated-fake-secret"
    root.execute("three")
    assert proxy.patch.call_count == 2
    assert (
        runtime._managed_sandbox_lookup_names("proxy-test", "thread:t", proxy.definition) == names
    )
    assert "fake-secret" not in caplog.text
    assert "sandbox resolve" in caplog.text


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("failure", ["missing", "resolve", "patch"])
def test_preparation_errors_do_not_execute_recreate_or_expose_secrets(
    proxy: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    asynchronous: bool,
    failure: str,
) -> None:
    error = (
        mda.ConnectionNotFoundError("sandbox-unavailable", "agent")
        if failure == "missing"
        else RuntimeError("HTTP 502 fake-secret")
    )
    if failure == "patch":
        proxy.patch.side_effect = error
    else:
        proxy.store.failure = error
    root = proxy.factory()

    async def invoke() -> object:
        # The synchronous path also proves the bridge works inside an event loop.
        return await root.aexecute("one") if asynchronous else root.execute("one")

    with pytest.raises(type(error)) as caught:
        asyncio.run(invoke())
    if failure == "missing":
        assert caught.value is error
    else:
        assert "proxy" in str(caught.value)
        assert "fake-secret" not in str(caught.value)
        assert caught.value.__cause__ is None
    assert "fake-secret" not in caplog.text
    assert proxy.store.calls[0]["identity"]["user"]["id"] == "alice"
    proxy.box.execute.assert_not_called()
    proxy.box.aexecute.assert_not_called()
    proxy.client.delete_sandbox.assert_not_called()
    assert proxy.patch.call_count == (1 if failure == "patch" else 0)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_keeps_sandbox_and_proxy_configuration_after_execute_failure(
    proxy: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    asynchronous: bool,
) -> None:
    command = proxy.box.aexecute if asynchronous else proxy.box.execute
    command.side_effect = [RuntimeError("HTTP 502"), "done"]
    root = proxy.factory()
    with pytest.raises(RuntimeError, match="HTTP 502"):
        asyncio.run(root.aexecute("one")) if asynchronous else root.execute("one")
    command.assert_called_once()
    proxy.client.delete_sandbox.assert_not_called()
    result = asyncio.run(root.aexecute("two")) if asynchronous else root.execute("two")
    assert result == "done"
    assert proxy.patch.call_count == 1


@pytest.mark.parametrize(
    "config",
    [
        {"rules": []},
        {"enabled": False},
        {"rules": [{"headers": [{"name": "X", "value": "static", "type": "workspace_secret"}]}]},
    ],
)
def test_static_proxy_reconciles_without_auth_and_preserves_legacy_names(
    proxy: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    config: dict[str, object],
) -> None:
    proxy.definition = mda.define_sandbox(proxy_config=config)
    assert runtime._legacy_sandbox_create_kwargs(proxy.definition) == {"proxy_config": config}
    assert runtime._sandbox_create_kwargs(proxy.definition)["proxy_config"] == config
    assert runtime._managed_sandbox_lookup_names("deployment", "thread:t", proxy.definition)[
        -1
    ] == runtime._legacy_managed_sandbox_name("deployment", "thread:t", {"proxy_config": config})
    auth = MagicMock(side_effect=AssertionError("static proxy must not read Agent Auth"))
    monkeypatch.setattr(_Connections, "resolve", auth)
    root = proxy.factory()
    root.execute("one")
    root.execute("two")
    proxy.patch.assert_called_once_with(proxy.definition, "existing-box", config)
    auth.assert_not_called()
    proxy.factory().execute("next run")
    assert proxy.patch.call_count == 2


def test_patch_endpoint_tenant_key_and_safe_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://host.example/prefix/")
    monkeypatch.setenv("LANGSMITH_API_KEY", "environment-key")
    monkeypatch.setenv("LANGSMITH_WORKSPACE_ID", "workspace")
    client = MagicMock()
    client.__enter__.return_value = client
    client.patch.return_value = httpx.Response(200)
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(httpx, "Client", factory)
    definition = mda.define_sandbox(api_key="option-key")
    patch_sandbox_proxy_config(definition, "box/name", {"rules": []})
    factory.assert_called_once_with(timeout=30, follow_redirects=False)
    client.patch.assert_called_once_with(
        "https://host.example/prefix/v2/sandboxes/boxes/box%2Fname",
        headers={"X-Tenant-Id": "workspace", "X-Api-Key": "option-key"},
        json={"proxy_config": {"rules": []}},
    )
    for failure in [httpx.Response(302, text="fake-secret"), RuntimeError("fake-secret")]:
        client.patch.side_effect = [failure]
        with pytest.raises(RuntimeError) as error:
            patch_sandbox_proxy_config(definition, "name", {})
        assert "fake-secret" not in str(error.value)
        assert error.value.__cause__ is None


def test_patch_prefers_deploy_owned_sandbox_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MDA_SANDBOX_API_ENDPOINT", "https://self-hosted.example/v2/sandboxes/")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://wrong.example/api/v1")
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    client = MagicMock()
    client.__enter__.return_value = client
    client.patch.return_value = httpx.Response(200)
    monkeypatch.setattr(httpx, "Client", MagicMock(return_value=client))

    patch_sandbox_proxy_config(mda.define_sandbox(), "box", {"rules": []})

    client.patch.assert_called_once_with(
        "https://self-hosted.example/v2/sandboxes/boxes/box",
        headers={},
        json={"proxy_config": {"rules": []}},
    )


@pytest.mark.parametrize("during_proxy_update", [False, True])
def test_replacement_receives_proxy_credentials_before_next_command(
    proxy: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    during_proxy_update: bool,
) -> None:
    root = proxy.factory()
    root.read("/workspace/file")
    replacement = MagicMock(id="new-backend")
    replacement.execute.return_value = "done"
    monkeypatch.setattr(runtime, "LangSmithSandbox", lambda **_: replacement)
    failure = ResourceNotFoundError("missing", "sandbox")
    command = proxy.box.execute
    if during_proxy_update:
        proxy.patch.side_effect = [failure, None]
    else:
        command.side_effect = failure
    proxy.client.get_sandbox.side_effect = ResourceNotFoundError("deleted", "sandbox")
    with pytest.raises(ResourceNotFoundError):
        root.execute("first")
    assert command.call_count == (0 if during_proxy_update else 1)
    replacement.execute.assert_not_called()
    assert proxy.patch.call_count == 1
    assert proxy.client.create_sandbox.call_args.kwargs["name"] == "existing-box"
    result = root.execute("next")
    assert result == "done"
    assert proxy.patch.call_count == 2
    proxy.client.delete_sandbox.assert_not_called()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("adopt", [False, True])
def test_box_without_proxy_config_clears_rules_once_per_run(
    proxy: SimpleNamespace,
    adopt: bool,
    asynchronous: bool,
) -> None:
    proxy.definition = mda.define_sandbox()
    if not adopt:
        proxy.client.get_sandbox.return_value = None
    for _ in range(2):
        root = proxy.factory()
        for command in ("one", "two"):
            asyncio.run(root.aexecute(command)) if asynchronous else root.execute(command)
    assert proxy.patch.call_count == 2
    assert all(call.args[2] == {"rules": []} for call in proxy.patch.call_args_list)


def test_unproxied_definition_clears_rules_each_run_after_proxied_run(
    proxy: SimpleNamespace,
) -> None:
    proxy.client.get_sandbox.return_value = None
    proxied = mda.define_sandbox(proxy_config={"rules": [{"name": "static"}]})
    unproxied = mda.define_sandbox()
    for definition in [proxied, unproxied, unproxied]:
        root = runtime._managed_sandbox_backend(
            definition,
            _config(),
            selection=runtime._sandbox_selection(definition),
        )
        assert root.execute("command") == "done"
    wires = [call.args[2] for call in proxy.patch.call_args_list]
    assert wires == [{"rules": [{"name": "static"}]}, {"rules": []}, {"rules": []}]
    proxy.client.create_sandbox.assert_called_once()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_unproxied_run_clears_rules_another_worker_applied(
    proxy: SimpleNamespace,
    asynchronous: bool,
) -> None:
    unproxied = mda.define_sandbox()
    proxied = mda.define_sandbox(proxy_config={"rules": [{"name": "other-worker"}]})
    selection = runtime._sandbox_selection(unproxied)
    assert selection == runtime._sandbox_selection(proxied)
    host_wire: dict[str, object] = {}

    def patch(_sandbox: object, _name: str, wire: dict[str, object]) -> None:
        host_wire.clear()
        host_wire.update(wire)

    proxy.patch.side_effect = patch

    def run(definition: SandboxDefinition) -> object:
        root = runtime._managed_sandbox_backend(definition, _config(), selection=selection)
        return asyncio.run(root.aexecute("command")) if asynchronous else root.execute("command")

    assert run(unproxied) == "done"
    worker_a_cache = dict(runtime._managed_sandboxes)
    assert host_wire == {"rules": []}

    # Worker B has an independent cache and configures the same Host box.
    runtime._managed_sandboxes.clear()
    assert run(proxied) == "done"
    assert host_wire == proxied.options["proxy_config"]

    runtime._managed_sandboxes.clear()
    runtime._managed_sandboxes.update(worker_a_cache)
    assert run(unproxied) == "done"
    assert proxy.patch.call_count == 3
    assert host_wire == {"rules": []}


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("missing_route", [False, True])
def test_failed_clear_on_adopted_box_blocks_commands_and_retries(
    proxy: SimpleNamespace,
    asynchronous: bool,
    missing_route: bool,
) -> None:
    proxy.definition = mda.define_sandbox()
    proxy.patch.side_effect = (
        ResourceNotFoundError("SENTINEL_BODY", "sandbox")
        if missing_route
        else RuntimeError("SENTINEL_BODY")
    )
    for _ in range(2):
        root = proxy.factory()
        error_type = ResourceNotFoundError if missing_route else RuntimeError
        with pytest.raises(error_type, match="Could not update sandbox proxy credentials") as error:
            asyncio.run(root.aexecute("blocked")) if asynchronous else root.execute("blocked")
        assert "SENTINEL_BODY" not in str(error.value)
    assert proxy.patch.call_count == 2
    executed = proxy.box.aexecute if asynchronous else proxy.box.execute
    executed.assert_not_called()
    proxy.patch.side_effect = None
    root = proxy.factory()
    asyncio.run(root.aexecute("recovered")) if asynchronous else root.execute("recovered")
    assert proxy.patch.call_count == 3
    executed.assert_called_once()


def test_failed_clear_of_rules_this_process_applied_fails_the_command(
    proxy: SimpleNamespace,
) -> None:
    proxy.client.get_sandbox.return_value = None
    proxy.factory().read("/workspace/file")
    proxy.definition = mda.define_sandbox()
    proxy.patch.side_effect = RuntimeError("down")
    with pytest.raises(RuntimeError, match="Could not update sandbox proxy credentials"):
        proxy.factory().execute("unproxied")
    proxy.patch.assert_called_once()
    proxy.box.execute.assert_not_called()


def test_proxy_patch_404_preserves_status_without_exposing_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    client.__enter__.return_value = client
    client.patch.return_value = httpx.Response(404, text="fake-secret")
    monkeypatch.setattr(httpx, "Client", MagicMock(return_value=client))
    with pytest.raises(ResourceNotFoundError) as caught:
        patch_sandbox_proxy_config(mda.define_sandbox(), "box", {})
    assert caught.value.resource_type == "sandbox"
    assert "fake-secret" not in str(caught.value)
    assert caught.value.__cause__ is None

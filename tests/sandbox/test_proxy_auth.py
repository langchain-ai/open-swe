"""Tests for GitHub proxy auth configuration."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest
from pydantic import JsonValue

from openswe.sandboxes.providers.langsmith import (
    PROXY_CONFIG_RETRY_WINDOW_SECONDS,
    configure_sandbox_proxy,
)


def _mock_async_client(mock_client_cls: MagicMock, inner: MagicMock) -> None:
    """Yield a client whose sandbox has no federation rules."""
    inner.get = AsyncMock(
        return_value=httpx2.Response(
            200, json={"proxy_config": {}}, request=httpx2.Request("GET", "https://sandbox.test")
        )
    )
    mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=inner)
    mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)


class TestConfigureSandboxProxy:
    """Tests for configure_sandbox_proxy payload shape and error handling."""

    async def test_without_token_sends_no_github_credentials(self) -> None:
        with (
            patch("openswe.sandboxes.providers.langsmith.httpx2.AsyncClient") as mock_client_cls,
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "ls-api-key"}),
        ):
            mock_client = MagicMock()
            mock_client.patch = AsyncMock(return_value=MagicMock())
            _mock_async_client(mock_client_cls, mock_client)

            await configure_sandbox_proxy(
                "sandbox-abc123",
                None,
                base_proxy_config={"rules": [{"name": "github", "headers": [{"value": "old"}]}]},
            )

            rules = mock_client.patch.call_args.kwargs["json"]["proxy_config"]["rules"]
            assert not {"github", "github-api"} & {rule["name"] for rule in rules}

    @pytest.mark.parametrize("existing_identity", ["absent", "different", "base-absent"])
    async def test_preserves_custom_proxy_config_when_adding_github_auth(
        self, existing_identity: str
    ) -> None:
        custom_rule = {"name": "public-api", "match_hosts": ["example.com"]}
        cloud_rules: list[dict[str, JsonValue]] = [
            {
                "name": "aws",
                "type": "aws",
                "enabled": True,
                "aws": {"role_arn": "arn:aws:iam::123456789012:role/ReadOnly"},
            },
            {
                "name": "gcp",
                "type": "gcp",
                "gcp": {
                    "workload_identity_provider": "//iam.googleapis.com/projects/123/locations/global/workloadIdentityPools/pool/providers/provider"
                },
            },
            {
                "name": "azure",
                "type": "azure",
                "azure": {
                    "tenant_id": "00000000-0000-0000-0000-000000000001",
                    "client_id": "00000000-0000-0000-0000-000000000002",
                },
            },
        ]
        existing_rules = deepcopy(cloud_rules) if existing_identity != "absent" else []
        for rule in existing_rules:
            rule["name"] = f"existing-{rule['name']}"
            settings = rule[str(rule["type"])]
            assert isinstance(settings, dict)
            settings["subject_scope"] = "tag:00000000-0000-0000-0000-000000000003"
        static_cloud_rule = {"name": "static-aws", "type": "aws", "aws": {"region": "us-east-1"}}
        with (
            patch("openswe.sandboxes.providers.langsmith.httpx2.AsyncClient") as mock_client_cls,
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "ls-api-key"}),
        ):
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_client.patch = AsyncMock(return_value=mock_response)
            _mock_async_client(mock_client_cls, mock_client)

            mock_client.get.return_value = httpx2.Response(
                200,
                json={"proxy_config": {"rules": existing_rules}},
                request=httpx2.Request("GET", "https://sandbox.test"),
            )
            base_rules = [] if existing_identity == "base-absent" else cloud_rules
            await configure_sandbox_proxy(
                "sandbox-abc123",
                "token",
                base_proxy_config={
                    "rules": [
                        custom_rule,
                        static_cloud_rule,
                        *base_rules,
                        {"name": "github", "headers": [{"value": "stale"}]},
                        {"name": "open-swe-thread-tools", "headers": [{"value": "stale"}]},
                    ],
                    "enabled": True,
                },
            )

        proxy_config = mock_client.patch.call_args.kwargs["json"]["proxy_config"]
        assert proxy_config["enabled"] is True
        assert proxy_config["rules"][2:] == [
            custom_rule,
            *([static_cloud_rule] if existing_identity == "absent" else []),
            *existing_rules,
        ]
        assert proxy_config["rules"][0]["headers"][0]["value"] == "Bearer token"
        assert [rule["name"] for rule in proxy_config["rules"][:2]] == ["github-api", "github"]

    async def test_retries_transient_http_error(self) -> None:
        """Transient proxy API errors should be retried on the same sandbox."""
        request = httpx2.Request(
            "PATCH", "https://api.smith.langchain.com/v2/sandboxes/boxes/sandbox-abc"
        )
        response = httpx2.Response(503, request=request, headers={"Retry-After": "7"})
        transient_error = httpx2.HTTPStatusError(
            "Server error",
            request=request,
            response=response,
        )
        with (
            patch("openswe.sandboxes.providers.langsmith.httpx2.AsyncClient") as mock_client_cls,
            patch("openswe.sandboxes.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "api-key"}),
        ):
            mock_client = MagicMock()
            failed_response = MagicMock()
            failed_response.raise_for_status.side_effect = transient_error
            successful_response = MagicMock()
            successful_response.raise_for_status = MagicMock()
            mock_client.patch = AsyncMock(side_effect=[failed_response, successful_response])
            _mock_async_client(mock_client_cls, mock_client)
            mock_client.get.side_effect = [response, mock_client.get.return_value]

            await configure_sandbox_proxy("sandbox-abc", "token")

            assert mock_client.get.call_count == 2
            assert mock_client.patch.call_count == 2
            assert mock_sleep.await_args_list == [((7.0,),), ((7.0,),)]

    @pytest.mark.parametrize("suspend_seconds", [170.0, PROXY_CONFIG_RETRY_WINDOW_SECONDS + 30])
    async def test_outlasts_a_host_suspend_within_the_retry_window(
        self, suspend_seconds: float
    ) -> None:
        """While the host suspends an idle sandbox the API fails the push with a 500."""
        request = httpx2.Request(
            "PATCH", "https://api.smith.langchain.com/v2/sandboxes/boxes/sandbox-abc"
        )
        error = httpx2.HTTPStatusError(
            "Server error",
            request=request,
            response=httpx2.Response(
                500, request=request, text="failed to push proxy config to sandbox"
            ),
        )
        clock = SimpleNamespace(now=0.0)

        async def sleep(delay: float) -> None:
            clock.now += delay

        def respond(*_args: object, **_kwargs: object) -> MagicMock:
            response = MagicMock()
            if clock.now < suspend_seconds:
                response.raise_for_status.side_effect = error
            return response

        with (
            patch("openswe.sandboxes.providers.langsmith.httpx2.AsyncClient") as mock_client_cls,
            patch("openswe.sandboxes.retry.asyncio.sleep", sleep),
            patch("openswe.sandboxes.retry.time", SimpleNamespace(monotonic=lambda: clock.now)),
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "api-key"}),
        ):
            mock_client = MagicMock()
            mock_client.patch = AsyncMock(side_effect=respond)
            _mock_async_client(mock_client_cls, mock_client)

            if suspend_seconds < PROXY_CONFIG_RETRY_WINDOW_SECONDS:
                await configure_sandbox_proxy("sandbox-abc", "token")
            else:
                with pytest.raises(httpx2.HTTPStatusError, match="failed to push proxy config"):
                    await configure_sandbox_proxy("sandbox-abc", "token")
                assert clock.now == PROXY_CONFIG_RETRY_WINDOW_SECONDS

    async def test_raises_on_non_retryable_http_error(self) -> None:
        """Non-retryable HTTP errors should propagate without retrying."""
        request = httpx2.Request(
            "PATCH", "https://api.smith.langchain.com/v2/sandboxes/boxes/sandbox-abc"
        )
        response = httpx2.Response(403, request=request)
        error = httpx2.HTTPStatusError("Forbidden", request=request, response=response)
        with (
            patch("openswe.sandboxes.providers.langsmith.httpx2.AsyncClient") as mock_client_cls,
            patch("openswe.sandboxes.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "api-key"}),
        ):
            mock_client = MagicMock()
            failed_response = MagicMock()
            failed_response.raise_for_status.side_effect = error
            mock_client.patch = AsyncMock(return_value=failed_response)
            _mock_async_client(mock_client_cls, mock_client)

            with pytest.raises(httpx2.HTTPStatusError):
                await configure_sandbox_proxy("sandbox-abc", "token")

            mock_client.patch.assert_called_once()
            mock_sleep.assert_not_called()


class TestSandboxReuse:
    @pytest.mark.asyncio
    async def test_a_sandbox_fails_unreachable_when_it_cannot_be_reached(self) -> None:
        """Writing the git identity is all that reaches such a sandbox, so its failure is the
        unreachable error the run's callers notify on, not the sandbox client's own."""
        from openswe.sandboxes.lifecycle import SandboxUnreachableError, _connect_existing_sandbox

        dead = MagicMock(id="sandbox-stale", aexecute=AsyncMock(side_effect=RuntimeError("gone")))

        with pytest.raises(SandboxUnreachableError) as excinfo:
            await _connect_existing_sandbox("thread-123", cached=dead, sandbox_id="sandbox-stale")

        assert excinfo.value.sandbox_id == "sandbox-stale"


_TAG = "00000000-0000-0000-0000-000000000001"
# The cloud identity rule a sandbox was created with.
_AWS_RULE = {
    "name": "aws",
    "type": "aws",
    "aws": {"role_arn": "arn:aws:iam::123456789012:role/ReadOnly", "subject_scope": f"tag:{_TAG}"},
}
# The cloud identity rule the workspace's sandboxes are created with today.
_CURRENT_AWS_RULE = {
    "name": "aws",
    "type": "aws",
    "aws": {"role_arn": "arn:aws:iam::123456789012:role/Current", "subject_scope": f"tag:{_TAG}"},
}
# An egress rule of the workspace.
_EGRESS_RULE = {"name": "mine-egress", "match_hosts": ["internal.mine.invalid"]}


@pytest.mark.parametrize("public", [True, False], ids=["public-origin", "local"])
@pytest.mark.parametrize("live_rules", [None, [], [_AWS_RULE]], ids=["new", "without", "with"])
def test_a_sandboxs_proxy_config_holds_no_credential_and_keeps_its_cloud_identity(
    monkeypatch: pytest.MonkeyPatch,
    public: bool,
    live_rules: list[dict[str, JsonValue]] | None,
) -> None:
    """LangSmith fixes a sandbox's cloud identity rules when it is created and refuses a write
    that changes them (HTTP 422), so an existing sandbox's config carries the ones read from
    it, never the ones the workspace names today; a new one is created with the workspace's.
    GitHub's and the tool channel's credentials come from the callback, never the config."""
    from openswe.sandboxes.proxy_callback import SandboxProxyCallback
    from openswe.sandboxes.tool_access import PROXY_CALLBACK_PATH

    monkeypatch.setenv(
        "DASHBOARD_API_BASE_URL", "https://robin.invalid" if public else "http://localhost:2024"
    )
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "secret")
    base = {
        "rules": [
            _CURRENT_AWS_RULE,
            _EGRESS_RULE,
            {"name": "github", "headers": [{"value": "stale"}]},
            {"name": "open-swe-thread-tools", "headers": [{"value": "stale"}]},
        ]
    }

    config = SandboxProxyCallback.proxy_config(base, federated_rules=live_rules)

    rules = config["rules"]
    assert isinstance(rules, list)
    assert rules == (
        [_CURRENT_AWS_RULE, _EGRESS_RULE] if live_rules is None else [_EGRESS_RULE, *live_rules]
    )
    assert "stale" not in json.dumps(config)
    if not public:
        assert "callbacks" not in config and SandboxProxyCallback.env_vars(github=True) == {}
        return
    assert config["callbacks"] == [
        {
            "match_hosts": ["api.github.com", "github.com", "*.github.com", "robin.invalid"],
            "url": f"https://robin.invalid{PROXY_CALLBACK_PATH}",
            "ttl_seconds": 300,
        }
    ]
    assert SandboxProxyCallback.env_vars(github=True) == {
        "GH_TOKEN": "proxy-injected",
        "OPEN_SWE_TOOLS_URL": "https://robin.invalid/dashboard/api/sandbox-tools",
    }
    # Without a token to replace it, the placeholder is empty, which gh reads as none.
    assert SandboxProxyCallback.env_vars(github=False) == {
        "GH_TOKEN": "",
        "OPEN_SWE_TOOLS_URL": "https://robin.invalid/dashboard/api/sandbox-tools",
    }


@pytest.mark.parametrize(
    "recorded",
    ["none", "this-sandbox", "another-sandbox", "other-env", "before-env", "env-refused"],
)
async def test_a_sandboxs_cloud_identity_is_read_once_and_recorded_on_its_thread(
    monkeypatch: pytest.MonkeyPatch, recorded: str
) -> None:
    """The rules are read once per sandbox, and again whenever the environment adopting it
    sets there changes, as when the backend's tunnel moves; an environment LangSmith did not
    take is not recorded, so the next run sets it again."""
    from openswe.sandboxes import managed
    from openswe.sandboxes.managed import FederatedRules
    from openswe.sandboxes.providers.langsmith import AdoptedSandbox
    from openswe.sandboxes.tool_access import SANDBOX_FEDERATED_RULES_KEY

    env = {"GH_TOKEN": "proxy-injected", "OPEN_SWE_TOOLS_URL": "https://new.invalid/tools"}
    metadata: dict[str, object] = {}
    if recorded not in {"none", "env-refused"}:
        record: dict[str, object] = {
            "sandbox_id": "sandbox-old" if recorded == "another-sandbox" else "sandbox-abc",
            "rules": [_CURRENT_AWS_RULE],
        }
        if recorded != "before-env":
            record["env_vars"] = (
                {**env, "OPEN_SWE_TOOLS_URL": "https://old.invalid/tools"}
                if recorded == "other-env"
                else env
            )
        metadata[SANDBOX_FEDERATED_RULES_KEY] = record
    read = AsyncMock(
        return_value=AdoptedSandbox(
            rules=[_AWS_RULE], env_vars=None if recorded == "env-refused" else env
        )
    )
    update = AsyncMock()
    monkeypatch.setattr(managed.LiveSandboxProxy, "adopt", read)
    monkeypatch.setattr(managed.THREADS, "update", update)

    rules = await FederatedRules.of("thread-123", metadata, "sandbox-abc", env_vars=env)

    if recorded == "this-sandbox":
        assert rules == [_CURRENT_AWS_RULE]
        read.assert_not_awaited()
        update.assert_not_awaited()
        return
    assert rules == [_AWS_RULE]
    assert read.await_args is not None and read.await_args.args == ("sandbox-abc",)
    assert read.await_args.kwargs["env_vars"] == env
    if recorded == "env-refused":
        update.assert_not_awaited()
    else:
        update.assert_awaited_once_with(
            "thread-123",
            metadata={
                SANDBOX_FEDERATED_RULES_KEY: {
                    "sandbox_id": "sandbox-abc",
                    "rules": [_AWS_RULE],
                    "env_vars": env,
                }
            },
        )


@pytest.mark.parametrize("env", ["stale", "held", "refused", "no-github"])
async def test_adopting_a_sandbox_sets_its_environment_once_keeping_its_run_config(
    monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    """A sandbox created before its commands' environment came from its own run config gets
    it in the one read of its cloud identity rules. LangSmith replaces the whole run config on
    an update, so the rest of it is kept; one that already holds it is not written, and one
    that lost its token gets an empty placeholder, since LangSmith keeps a key an update
    leaves out. One LangSmith refuses still gives the rules, which are what the run needs."""
    from openswe.sandboxes.providers import langsmith

    env_vars = {
        "GH_TOKEN": "" if env == "no-github" else "proxy-injected",
        "OPEN_SWE_TOOLS_URL": "https://robin.invalid/tools",
    }
    held = (
        env_vars
        if env == "held"
        else {"GH_TOKEN": "proxy-injected", "OPEN_SWE_TOOLS_URL": "https://robin.invalid/tools"}
        if env == "no-github"
        else {"GH_TOKEN": "stale"}
    )
    run_config: dict[str, object] = {
        "user": "agent",
        "work_dir": "/workspace",
        "env_vars": {"LANG": "C.UTF-8", **held},
    }
    live = {"proxy_config": {"rules": [_EGRESS_RULE, _AWS_RULE]}, "run_config": run_config}
    patched: list[dict[str, object]] = []

    async def write(_client: object, url: str, payload: dict[str, object], *_args: object) -> None:
        assert url.endswith("/boxes/sandbox-abc")
        patched.append(payload)
        if env == "refused":
            request = httpx2.Request("PATCH", url)
            raise httpx2.HTTPStatusError(
                "422", request=request, response=httpx2.Response(422, request=request)
            )

    monkeypatch.setenv("LANGSMITH_API_KEY", "key")
    monkeypatch.setattr(langsmith.LiveSandboxProxy, "live", AsyncMock(return_value=live))
    monkeypatch.setattr(langsmith, "_patch_sandbox", write)

    adopted = await langsmith.LiveSandboxProxy.adopt("sandbox-abc", env_vars=env_vars)

    assert adopted is not None and adopted.rules == [_AWS_RULE]
    assert adopted.env_vars == (None if env == "refused" else env_vars)
    if env == "held":
        assert patched == []
    else:
        assert patched == [
            {
                "run_config": {
                    "user": "agent",
                    "work_dir": "/workspace",
                    "env_vars": {"LANG": "C.UTF-8", **env_vars},
                }
            }
        ]

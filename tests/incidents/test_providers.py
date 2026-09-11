import time
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from langchain_core.tools import StructuredTool, ToolException

from agent.dashboard import workspace_mcps
from agent.incidents import service
from agent.incidents.models import Incident, IncidentPolicy
from agent.mcp.models import MCPConnection


class Remote:
    def __init__(self):
        self.incidents = {
            "INC1": {
                "id": "INC1",
                "name": "API unavailable",
                "permalink": "https://app.incident.io/acme/incidents/INC1",
                "incident_status": {"id": "open", "name": "Investigating"},
                "severity": {"id": "sev1", "name": "Critical"},
                "slack_channel_id": "C1",
            }
        }
        self.calls = []
        self.fail_read = False
        self.uncertain_write = False
        self.response_mode = "structured"
        self.schemas = {
            "incident_show": {
                "type": "object",
                "properties": {"incident_id": {"type": "string"}},
                "required": ["incident_id"],
            },
            "incident_list": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
            "incident_update": {
                "type": "object",
                "properties": {
                    "incident_id": {"type": "string"},
                    "status_id": {"type": "string"},
                    "severity_id": {"type": "string"},
                },
                "required": ["incident_id"],
            },
            "resource_show": {
                "type": "object",
                "properties": {"resource": {"type": "string", "enum": ["organisation"]}},
                "required": ["resource"],
            },
        }

    async def tools(self, *sources, connection_name):
        assert sources == (workspace_mcps.workspace_mcp_source,)
        connection = await workspace_mcps.get_workspace_mcp(connection_name)
        result = []
        for name, schema in self.schemas.items():
            if name not in connection.allowed_tools:
                continue

            async def call(_name=name, **arguments):
                self.calls.append((_name, arguments))
                if self.response_mode == "error":
                    raise ToolException('{"incident":{"id":"INC1","name":"Not success"}}')
                if self.fail_read:
                    raise TimeoutError("sensitive upstream error")
                if _name == "resource_show":
                    content = {
                        "organisation": {
                            "incident_statuses": [
                                {"id": "open", "name": "Investigating"},
                                {"id": "closed", "name": "Resolved"},
                            ],
                            "severities": [
                                {"id": "sev1", "name": "Critical"},
                                {"id": "sev3", "name": "Low"},
                            ],
                        }
                    }
                elif _name == "incident_list":
                    content = {"incidents": list(self.incidents.values())}
                elif _name == "incident_show":
                    content = {"incident": self.incidents[arguments["incident_id"]]}
                else:
                    incident = self.incidents[arguments["incident_id"]]
                    if "status_id" in arguments:
                        incident["incident_status"] = {
                            "id": arguments["status_id"],
                            "name": "Resolved",
                        }
                    if "severity_id" in arguments:
                        incident["severity"] = {"id": arguments["severity_id"], "name": "Low"}
                    if self.uncertain_write:
                        raise TimeoutError("response lost")
                    content = {"incident": incident}
                if self.response_mode == "text":
                    import json

                    return [{"type": "text", "text": json.dumps(content)}], None
                if self.response_mode == "unstructured":
                    return "Unstructured provider response", None
                if self.response_mode == "artifact_error":
                    return [], {"isError": True, "structured_content": content}
                return [], {"structured_content": content}

            result.append(
                StructuredTool.from_function(
                    coroutine=call,
                    name=f"normalized_{name}_hash",
                    description=name,
                    args_schema=schema,
                    metadata={"mcp_tool_name": name},
                    response_format="content_and_artifact",
                    handle_tool_error=True,
                )
            )
        return result


@pytest.fixture
async def configured(fake_store, monkeypatch):
    from agent.incidents import providers

    await service.POLICIES.put("default", IncidentPolicy(enabled=True, workspace_id="T1"))
    record = Incident(
        id="local",
        workspace_id="T1",
        channel_id="C1",
        thread_id="thread",
        status="watching",
        can_read=True,
        joined=True,
        last_verified_at=time.time(),
    )
    await service.INVESTIGATIONS.put(record.id, record)
    await workspace_mcps._store.put(
        "incident",
        MCPConnection(
            name="incident",
            url="https://mcp.incident.io/mcp",
            revision="r1",
            updated_at="now",
            allowed_tools=["incident_show", "incident_list", "incident_update", "resource_show"],
        ),
    )
    remote = Remote()
    monkeypatch.setattr(providers, "load_mcp_tools", remote.tools)
    monkeypatch.setattr(service, "wake", AsyncMock())
    monkeypatch.setattr(
        service.slack,
        "channel_info",
        AsyncMock(
            return_value={
                "id": "C1",
                "name": "inc-api",
                "is_private": False,
                "is_member": True,
                "is_channel": True,
                "is_ext_shared": False,
                "is_pending_ext_shared": False,
            }
        ),
    )
    return record, remote


async def attach(record):
    from agent.incidents import providers

    accepted = await providers.submit_provider_command(
        record.id,
        "attach",
        {"connection_name": "incident", "external_id": "INC1"},
        "attach",
        {"id": "github:responder"},
    )
    receipt = await service.RECEIPTS.get(accepted["command_id"])
    assert await providers.process_provider_receipt(record, receipt)
    return accepted


async def test_attach_is_durable_and_only_worker_writes_binding(configured):
    from agent.incidents import providers

    record, _ = configured
    accepted = await providers.submit_provider_command(
        record.id,
        "attach",
        {"connection_name": "incident", "external_id": "INC1"},
        "request",
        {"id": "github:responder"},
    )
    assert await providers.BINDINGS.get(record.id) is None
    receipt = await service.RECEIPTS.get(accepted["command_id"])
    assert receipt.kind == "provider_command"
    await providers.process_provider_receipt(record, receipt)
    context = await providers.provider_context(record.id)
    assert context["binding"]["external_id"] == "INC1"
    assert context["snapshot"]["status"] == {"id": "open", "name": "Investigating"}
    assert context["capabilities"]["status_page_publish"] == "unsupported"
    assert (await providers.get_operation(record.id, receipt.id))["status"] == "succeeded"


@pytest.mark.parametrize("change", ["missing", "disabled", "permission", "endpoint"])
async def test_attachment_rejects_unavailable_or_untrusted_connection(configured, change):
    from agent.incidents import providers

    record, _ = configured
    connection = await workspace_mcps._store.get("incident")
    if change == "missing":
        await workspace_mcps._store.delete("incident")
    else:
        if change == "disabled":
            connection.enabled = False
        elif change == "permission":
            connection.allowed_tools = ["incident_list"]
        else:
            connection.url = "https://example.com/mcp"
        await workspace_mcps._store.put("incident", connection)
    with pytest.raises(HTTPException):
        await providers.submit_provider_command(
            record.id,
            "attach",
            {"connection_name": "incident", "external_id": "INC1"},
            "request",
            {"id": "github:responder"},
        )
    assert not await service.RECEIPTS.search_all()


async def test_revocation_hides_cached_provider_content_and_prevents_queued_attach(configured):
    from agent.incidents import providers

    record, remote = configured
    result = await attach(record)
    connection = await workspace_mcps._store.get("incident")
    connection.allowed_tools = []
    await workspace_mcps._store.put("incident", connection)
    context = await providers.provider_context(record.id)
    assert context["snapshot"] is None
    assert context["error_kind"] == "permission_denied"
    before = len(remote.calls)
    await providers.refresh_provider(record)
    assert len(remote.calls) == before
    assert (await providers.get_operation(record.id, result["command_id"]))["status"] == "succeeded"


async def test_provider_outage_retains_last_sync_and_does_not_block_slack_receipts(configured):
    from agent.incidents import providers

    record, remote = configured
    await attach(record)
    previous = await providers.BINDINGS.get(record.id)
    remote.fail_read = True
    await providers.refresh_provider(record)
    current = await providers.BINDINGS.get(record.id)
    assert current.last_synced_at == previous.last_synced_at
    assert current.snapshot.status.id == "open"
    assert current.error_kind == "unavailable"
    assert "sensitive" not in current.error
    assert await service.RECEIPTS.search_all()


async def test_status_write_is_bound_explicit_and_uncertain_delivery_is_not_repeated(configured):
    from agent.incidents import providers

    record, remote = configured
    await attach(record)
    accepted = await providers.submit_provider_command(
        record.id, "status", {"status_id": "closed"}, "resolve", {"id": "github:responder"}
    )
    assert not [call for call in remote.calls if call[0] == "incident_update"]
    receipt = await service.RECEIPTS.get(accepted["command_id"])
    remote.uncertain_write = True
    await providers.process_provider_receipt(record, receipt)
    await providers.process_provider_receipt(record, receipt)
    assert len([call for call in remote.calls if call[0] == "incident_update"]) == 1
    operation = await providers.get_operation(record.id, receipt.id)
    assert operation["status"] == "succeeded"
    assert (await providers.BINDINGS.get(record.id)).snapshot.status.id == "closed"
    assert record.status == "watching"


async def test_unknown_update_schema_disables_capability_without_using_broad_tool(configured):
    from agent.incidents import providers

    record, remote = configured
    remote.schemas["incident_update"] = {
        "type": "object",
        "properties": {"instructions": {"type": "string"}},
        "required": ["instructions"],
    }
    await attach(record)
    assert (await providers.provider_context(record.id))["capabilities"][
        "update_status"
    ] == "unsupported"
    with pytest.raises(HTTPException) as error:
        await providers.submit_provider_command(
            record.id, "status", {"status_id": "closed"}, "resolve", {"id": "github:responder"}
        )
    assert error.value.status_code == 422
    assert not [call for call in remote.calls if call[0] == "incident_update"]


async def test_external_history_requires_visible_channel_and_never_enrolls(configured):
    from agent.incidents import providers

    record, remote = configured
    remote.incidents["HIDDEN"] = {"id": "HIDDEN", "name": "Sensitive incident"}
    result = await providers.search_external(record.id, "incident", "API")
    assert [item["external_id"] for item in result["items"]] == ["INC1"]
    assert result["gaps"]
    assert len(await service.INVESTIGATIONS.search_all()) == 1
    assert not await providers.BINDINGS.search_all()
    with pytest.raises(HTTPException) as error:
        await providers.read_external(record.id, "incident", "HIDDEN")
    assert error.value.status_code == 404


async def test_provider_reads_and_commands_recheck_local_incident_access(configured):
    from agent.incidents import providers

    record, _ = configured
    await attach(record)
    policy = await service.get_policy()
    policy.workspace_id = "OTHER"
    await service.POLICIES.put("default", policy)
    for read in [
        providers.provider_context(record.id),
        providers.search_external(record.id, "incident", "API"),
        providers.read_external(record.id, "incident", "INC1"),
    ]:
        with pytest.raises(HTTPException) as error:
            await read
        assert error.value.status_code == 404


async def test_schema_required_fields_are_fail_closed(configured):
    record, remote = configured
    remote.schemas["incident_show"]["required"].append("unknown_required")
    with pytest.raises(HTTPException) as error:
        await attach(record)
    assert error.value.status_code == 422
    assert not remote.calls


async def test_refresh_reconciles_uncertain_write_after_receipt_cleanup(configured):
    from agent.incidents import providers

    record, remote = configured
    await attach(record)
    accepted = await providers.submit_provider_command(
        record.id, "status", {"status_id": "closed"}, "resolve", {"id": "github:responder"}
    )
    receipt = await service.RECEIPTS.get(accepted["command_id"])
    remote.uncertain_write = True
    await providers.process_provider_receipt(record, receipt)
    assert (await providers.get_operation(record.id, receipt.id))["status"] == "unknown"
    await service.RECEIPTS.delete(receipt.id)
    await providers.refresh_provider(record)
    assert (await providers.get_operation(record.id, receipt.id))["status"] == "succeeded"
    assert len([call for call in remote.calls if call[0] == "incident_update"]) == 1


async def test_discovered_argument_constraints_are_validated_before_queueing(configured):
    from agent.incidents import providers

    record, remote = configured
    await attach(record)
    remote.schemas["incident_update"]["properties"]["status_id"]["pattern"] = "^status_"
    with pytest.raises(HTTPException) as error:
        await providers.submit_provider_command(
            record.id, "status", {"status_id": "invalid"}, "request", {"id": "github:responder"}
        )
    assert error.value.status_code == 422
    assert len(await service.RECEIPTS.search_all()) == 1


async def test_provider_http_routes_enforce_responder_auth_and_reject_extra_write_fields(
    configured, monkeypatch
):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from agent.dashboard import oauth
    from agent.incidents import provider_api

    record, _ = configured
    app = FastAPI()
    app.include_router(provider_api.router, prefix="/providers")
    monkeypatch.setenv("OBSERVABILITY_AUTHORIZED_EMAILS", "sre@example.com")
    app.dependency_overrides[oauth.require_session] = lambda: {
        "sub": "developer",
        "email": "dev@example.com",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(f"/providers/{record.id}")).status_code == 403
        assert (
            await client.post(
                "/providers/search",
                json={"incident_id": record.id, "connection_name": "incident", "query": "api"},
            )
        ).status_code == 403
        app.dependency_overrides[oauth.require_session] = lambda: {
            "sub": "sre",
            "email": "sre@example.com",
        }
        rejected = await client.post(
            f"/providers/{record.id}/attach",
            json={
                "connection_name": "incident",
                "external_id": "INC1",
                "request_id": "attach",
                "actor": "admin",
            },
        )
        assert rejected.status_code == 422
        response = await client.post(
            f"/providers/{record.id}/attach",
            json={"connection_name": "incident", "external_id": "INC1", "request_id": "attach"},
        )
        assert response.status_code == 202
        receipt = await service.RECEIPTS.get(response.json()["command_id"])
        assert receipt.actor["id"] == "github:sre"
        assert receipt.payload["connection_name"] == "incident"


@pytest.mark.parametrize("response_mode", ["error", "unstructured", "artifact_error"])
async def test_provider_error_content_cannot_create_successful_attachment(
    configured, response_mode
):
    from agent.incidents import providers

    record, remote = configured
    remote.response_mode = response_mode
    accepted = await attach(record)
    assert await providers.BINDINGS.get(record.id) is None
    assert (await providers.get_operation(record.id, accepted["command_id"]))["status"] == "failed"


async def test_json_text_output_is_accepted_without_discarding_artifact_errors(configured):
    from agent.incidents import providers

    record, remote = configured
    remote.response_mode = "text"
    await attach(record)
    assert (await providers.provider_context(record.id))["snapshot"]["title"] == "API unavailable"


async def test_queued_attach_rechecks_connection_and_remote_channel(configured):
    from agent.incidents import providers

    record, remote = configured
    accepted = await providers.submit_provider_command(
        record.id,
        "attach",
        {"connection_name": "incident", "external_id": "INC1"},
        "request",
        {"id": "github:responder"},
    )
    remote.incidents["INC1"]["slack_channel_id"] = "C2"
    receipt = await service.RECEIPTS.get(accepted["command_id"])
    await providers.process_provider_receipt(record, receipt)
    assert await providers.BINDINGS.get(record.id) is None
    assert (await providers.get_operation(record.id, receipt.id))["status"] == "failed"


async def test_postmortem_include_uses_discovered_array_schema(configured):
    from agent.incidents import providers

    record, remote = configured
    remote.schemas["incident_show"]["properties"]["include"] = {
        "type": "array",
        "items": {"type": "string", "enum": ["investigation", "postmortem"]},
    }
    remote.incidents["INC1"]["postmortem"] = {"content": "Restored the service"}
    await attach(record)
    show_calls = [arguments for name, arguments in remote.calls if name == "incident_show"]
    assert show_calls[0]["include"] == ["investigation", "postmortem"]
    assert (await providers.provider_context(record.id))["snapshot"][
        "postmortem"
    ] == "Restored the service"


async def test_operation_dedupe_survives_receipt_cleanup(configured):
    from agent.incidents import providers

    record, _ = configured
    accepted = await attach(record)
    await service.RECEIPTS.delete(accepted["command_id"])
    duplicate = await providers.submit_provider_command(
        record.id,
        "attach",
        {"connection_name": "incident", "external_id": "INC1"},
        "attach",
        {"id": "github:responder"},
    )
    assert duplicate["status"] == "duplicate"
    with pytest.raises(HTTPException) as error:
        await providers.submit_provider_command(
            record.id,
            "attach",
            {"connection_name": "incident", "external_id": "INC2"},
            "attach",
            {"id": "github:responder"},
        )
    assert error.value.status_code == 409
    operation = await providers.get_operation(record.id, accepted["command_id"])
    assert "external_id" not in operation
    assert "actor" not in operation


async def test_malformed_provider_list_returns_unsupported_shape(configured):
    from agent.incidents import providers

    record, remote = configured
    remote.incidents["broken"] = "not an incident"
    with pytest.raises(HTTPException) as error:
        await providers.search_external(record.id, "incident", "api")
    assert error.value.status_code == 422


async def test_provider_configuration_exposes_named_status_and_severity_options(configured):
    from agent.incidents import providers

    record, remote = configured
    await attach(record)
    context = await providers.provider_context(record.id)
    assert context["status_options"] == [
        {"id": "open", "name": "Investigating"},
        {"id": "closed", "name": "Resolved"},
    ]
    assert context["severity_options"] == [
        {"id": "sev1", "name": "Critical"},
        {"id": "sev3", "name": "Low"},
    ]
    assert context["configuration_error"] is None
    assert [args for name, args in remote.calls if name == "resource_show"] == [
        {"resource": "organisation"}
    ]


async def test_unknown_configuration_schema_disables_writes_but_preserves_incident_read(configured):
    from agent.incidents import providers

    record, remote = configured
    remote.schemas["resource_show"]["properties"]["resource"]["enum"] = ["other"]
    await attach(record)
    context = await providers.provider_context(record.id)
    assert context["snapshot"]["title"] == "API unavailable"
    assert context["status_options"] == []
    assert context["configuration_error_kind"] == "unsupported"
    assert context["capabilities"]["update_status"] == "unsupported"
    assert not [call for call in remote.calls if call[0] == "resource_show"]


async def test_status_commands_require_provider_organisation_ids(configured):
    from agent.incidents import providers

    record, remote = configured
    await attach(record)
    with pytest.raises(HTTPException) as error:
        await providers.submit_provider_command(
            record.id, "status", {"status_id": "unknown"}, "status", {"id": "github:responder"}
        )
    assert error.value.status_code == 422
    assert not [call for call in remote.calls if call[0] == "incident_update"]


async def test_default_provider_is_available_before_attachment_and_exclusion_revokes_access(
    configured,
):
    from agent.incidents import providers

    record, _ = configured
    policy = await service.get_policy()
    policy.provider_connection_name = "incident"
    await service.POLICIES.put("default", policy)
    context = await providers.provider_context(record.id)
    assert context["binding"] is None
    assert context["default_connection_name"] == "incident"
    policy.excluded_channel_ids = [record.channel_id]
    await service.POLICIES.put("default", policy)
    with pytest.raises(HTTPException) as error:
        await providers.provider_context(record.id)
    assert error.value.status_code == 404


async def test_malformed_or_credentialed_provider_link_is_not_exposed(configured):
    from agent.incidents import providers

    record, remote = configured
    remote.incidents["INC1"]["permalink"] = "https://["
    await attach(record)
    assert (await providers.provider_context(record.id))["snapshot"]["url"] == ""
    remote.incidents["INC1"]["permalink"] = "https://password@app.incident.io/acme/incidents/INC1"
    await providers.refresh_provider(record)
    assert (await providers.provider_context(record.id))["snapshot"]["url"] == ""


async def test_analysis_scope_changes_with_binding_or_connection_revision(configured):
    from agent.incidents import providers

    record, remote = configured
    assert await providers.analysis_scope(record) == ""
    await attach(record)
    scope = await providers.analysis_scope(record)
    assert scope and await providers.analysis_scope(record) == scope
    binding = await providers.BINDINGS.get(record.id)
    binding.snapshot.title = "Updated public summary"
    await providers.BINDINGS.put(record.id, binding)
    assert await providers.analysis_scope(record) == scope
    connection = await workspace_mcps._store.get("incident")
    connection.revision = "r2"
    connection.updated_at = "later"
    await workspace_mcps._store.put("incident", connection)
    with pytest.raises(PermissionError):
        await providers.analysis_scope(record)
    await providers.refresh_provider(record)
    changed = await providers.analysis_scope(record)
    assert changed != scope
    remote.incidents["INC2"] = {**remote.incidents["INC1"], "id": "INC2"}
    accepted = await providers.submit_provider_command(
        record.id,
        "attach",
        {"connection_name": "incident", "external_id": "INC2"},
        "rebind",
        {"id": "github:responder"},
    )
    await providers.process_provider_receipt(
        record, await service.RECEIPTS.get(accepted["command_id"])
    )
    assert await providers.analysis_scope(record) != changed
    assert [name for name, _ in remote.calls] == ["incident_show", "incident_show", "incident_show"]


@pytest.mark.parametrize(
    "change",
    [
        "missing_connection",
        "disabled",
        "read_permission",
        "endpoint",
        "workspace",
        "binding_channel",
        "binding_incident",
        "snapshot_channel",
        "snapshot_incident",
        "missing_snapshot",
        "persisted_denial",
    ],
)
async def test_analysis_scope_rejects_revoked_or_rebound_provider_identity(configured, change):
    from agent.incidents import providers

    record, _ = configured
    await attach(record)
    connection = await workspace_mcps._store.get("incident")
    binding = await providers.BINDINGS.get(record.id)
    if change == "missing_connection":
        await workspace_mcps._store.delete("incident")
    else:
        if change == "disabled":
            connection.enabled = False
        elif change == "read_permission":
            connection.allowed_tools = ["incident_list"]
        elif change == "endpoint":
            connection.url = "https://example.com/mcp"
        await workspace_mcps._store.put("incident", connection)
    if change == "workspace":
        binding.workspace_id = "other"
    elif change == "binding_channel":
        binding.channel_id = "other"
    elif change == "binding_incident":
        binding.incident_id = "other"
    elif change == "snapshot_channel":
        binding.snapshot.slack_channel_id = "other"
    elif change == "snapshot_incident":
        binding.snapshot.external_id = "other"
    elif change == "missing_snapshot":
        binding.snapshot = None
    elif change == "persisted_denial":
        binding.error_kind = "permission_denied"
    await providers.BINDINGS.put(record.id, binding)
    with pytest.raises(PermissionError):
        await providers.analysis_scope(record)


async def test_analysis_scope_rechecks_slack_visibility_without_provider_calls(
    configured, monkeypatch
):
    from agent.incidents import providers

    record, remote = configured
    await attach(record)
    monkeypatch.setattr(
        service.slack,
        "channel_info",
        AsyncMock(
            return_value={
                "id": "C1",
                "is_channel": True,
                "is_member": True,
                "is_private": True,
                "is_ext_shared": False,
                "is_pending_ext_shared": False,
            }
        ),
    )
    with pytest.raises(PermissionError):
        await providers.analysis_scope(record)
    assert [name for name, _ in remote.calls] == ["incident_show"]


async def test_persisted_provider_permission_denial_hides_stale_snapshot_until_refresh(configured):
    from agent.incidents import providers

    record, _ = configured
    await attach(record)
    binding = await providers.BINDINGS.get(record.id)
    binding.error_kind = "permission_denied"
    binding.error = "Provider association changed"
    await providers.BINDINGS.put(record.id, binding)
    context = await providers.provider_context(record.id)
    assert context["binding"] is None
    assert context["snapshot"] is None
    assert context["error_kind"] == "permission_denied"
    await providers.refresh_provider(record)
    assert (await providers.provider_context(record.id))["snapshot"]["external_id"] == "INC1"


async def test_changed_connection_and_failed_refresh_cannot_reuse_old_snapshot(configured):
    from agent.incidents import providers

    record, remote = configured
    await attach(record)
    before = await providers.BINDINGS.get(record.id)
    connection = await workspace_mcps._store.get("incident")
    connection.revision = "new-credentials"
    await workspace_mcps._store.put("incident", connection)
    remote.fail_read = True
    await providers.refresh_provider(record)
    stale = await providers.BINDINGS.get(record.id)
    assert stale.snapshot_scope == before.snapshot_scope
    assert stale.error_kind == "unavailable"
    with pytest.raises(PermissionError):
        await providers.analysis_scope(record)
    assert (await providers.provider_context(record.id))["snapshot"] is None
    remote.fail_read = False
    await providers.refresh_provider(record)
    assert (await providers.BINDINGS.get(record.id)).snapshot_scope != before.snapshot_scope
    assert await providers.analysis_scope(record)
    assert (await providers.provider_context(record.id))["snapshot"]["external_id"] == "INC1"


async def test_legacy_snapshot_requires_refresh_before_analysis_or_display(configured):
    from agent.incidents import providers

    record, _ = configured
    await attach(record)
    binding = await providers.BINDINGS.get(record.id)
    binding.snapshot_scope = ""
    await providers.BINDINGS.put(record.id, binding)
    with pytest.raises(PermissionError):
        await providers.analysis_scope(record)
    assert (await providers.provider_context(record.id))["snapshot"] is None
    await providers.refresh_provider(record)
    assert await providers.analysis_scope(record)

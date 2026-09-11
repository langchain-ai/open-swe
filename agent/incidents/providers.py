"""Scoped incident.io MCP reads and durable, responder-requested lifecycle changes."""

import json
import logging
import time
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import HTTPException
from jsonschema import Draft202012Validator
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from referencing import Registry

from agent.dashboard.workspace_mcps import (
    get_workspace_mcp,
    list_workspace_mcp_records,
    workspace_mcp_source,
)
from agent.incidents import service
from agent.incidents.models import Incident, Receipt
from agent.incidents.provider_models import (
    ProviderBinding,
    ProviderOperation,
    ProviderSnapshot,
    ProviderValue,
)
from agent.mcp.runtime import load_mcp_tools
from agent.store import TypedStore, now_iso

logger = logging.getLogger(__name__)
BINDINGS = TypedStore(["incidents", "provider_bindings"], ProviderBinding)
OPERATIONS = TypedStore(["incidents", "provider_operations"], ProviderOperation)
_ENDPOINT = "https://mcp.incident.io/mcp"
_FIELDS = {
    "external_id": ("incident_id", "id"),
    "status_id": ("incident_status_id", "status_id"),
    "severity_id": ("severity_id",),
    "query": ("query", "search"),
    "resource": ("resource",),
}
_TOOLS = {"incident_show", "incident_list", "incident_update", "resource_show"}


class ProviderError(Exception):
    def __init__(self, kind: str, message: str):
        self.kind = kind
        super().__init__(message)


def _http(error: ProviderError) -> HTTPException:
    return HTTPException(
        {"unsupported": 422, "permission_denied": 403, "unavailable": 503}[error.kind],
        str(error),
    )


async def _accessible(incident_id: str) -> Incident:
    record = await service.INVESTIGATIONS.get(incident_id)
    policy = await service.get_policy()
    if (
        not record
        or record.workspace_id != policy.workspace_id
        or record.channel_id in policy.excluded_channel_ids
        or not await service.readable(record, policy)
    ):
        raise HTTPException(404, "Incident not found")
    return record


def _schema(tool: BaseTool) -> dict[str, Any]:
    schema = tool.args_schema
    return schema if isinstance(schema, dict) else {}


def _arguments(
    tool: BaseTool, values: dict[str, str], *, validate_values: bool = True
) -> dict[str, Any]:
    schema = _schema(tool)
    properties = schema.get("properties", {})
    if schema.get("type") != "object" or not isinstance(properties, dict):
        raise ProviderError("unsupported", "Provider tool has an unsupported input schema")
    try:
        Draft202012Validator.check_schema(schema)
    except Exception:
        raise ProviderError(
            "unsupported", "Provider tool has an unsupported input schema"
        ) from None
    arguments: dict[str, Any] = {}
    for field, value in values.items():
        name = next((name for name in _FIELDS[field] if name in properties), None)
        if (
            name is None
            or not isinstance(properties[name], dict)
            or properties[name].get("type") != "string"
        ):
            raise ProviderError("unsupported", "Provider tool has an unsupported input schema")
        choices = properties[name].get("enum")
        if validate_values and choices is not None and value not in choices:
            raise ProviderError("unsupported", "Value is not supported by the provider tool schema")
        arguments[name] = value
    if (tool.metadata or {}).get("mcp_tool_name") == "incident_show":
        include = properties.get("include", {})
        if isinstance(include, dict) and include.get("type") == "array":
            item = include.get("items", {})
            if isinstance(item, dict) and item.get("type") == "string":
                arguments["include"] = [
                    value
                    for value in ("investigation", "postmortem")
                    if value in item.get("enum", ("investigation", "postmortem"))
                ]
    if not set(schema.get("required", [])).issubset(arguments):
        raise ProviderError("unsupported", "Provider tool requires unsupported input fields")
    try:
        if validate_values:
            Draft202012Validator(schema, registry=Registry()).validate(arguments)
    except Exception:
        raise ProviderError(
            "unsupported", "Arguments do not match the discovered provider schema"
        ) from None
    return arguments


async def _catalog(connection_name: str) -> dict[str, BaseTool]:
    connection = await get_workspace_mcp(connection_name)
    if not connection or not connection.enabled:
        raise ProviderError("permission_denied", "Provider connection is disabled or disconnected")
    if connection.url.rstrip("/") != _ENDPOINT:
        raise ProviderError(
            "unsupported", "Select the official incident.io workspace MCP connection"
        )
    if "incident_show" not in connection.allowed_tools:
        raise ProviderError("permission_denied", "The incident_show tool is not allowed")
    tools = await load_mcp_tools(workspace_mcp_source, connection_name=connection_name)
    catalog: dict[str, BaseTool] = {}
    for tool in tools:
        name = (tool.metadata or {}).get("mcp_tool_name")
        if isinstance(name, str) and name in _TOOLS:
            catalog[name] = tool
    if "incident_show" not in catalog:
        raise ProviderError("unavailable", "Provider tool discovery is unavailable")
    return catalog


def _capabilities(catalog: dict[str, BaseTool], allowed: list[str]) -> dict[str, str]:
    result = {"postmortem_write": "unsupported", "status_page_publish": "unsupported"}
    for capability, tool_name, values in (
        ("read", "incident_show", {"external_id": "id"}),
        ("search", "incident_list", {"query": "query"}),
        ("update_status", "incident_update", {"external_id": "id", "status_id": "id"}),
        ("update_severity", "incident_update", {"external_id": "id", "severity_id": "id"}),
    ):
        if tool_name not in allowed:
            result[capability] = "permission_denied"
        elif tool_name not in catalog:
            result[capability] = "unavailable"
        else:
            try:
                _arguments(catalog[tool_name], values, validate_values=False)
                result[capability] = "supported"
            except ProviderError:
                result[capability] = "unsupported"
    return result


async def validate_provider_connection(name: str) -> None:
    try:
        catalog = await _catalog(name)
        _arguments(catalog["incident_show"], {"external_id": "id"}, validate_values=False)
    except ProviderError as exc:
        raise _http(exc) from None


async def _invoke(
    catalog: dict[str, BaseTool], name: str, values: dict[str, str]
) -> dict[str, Any]:
    if name not in catalog:
        raise ProviderError("permission_denied", "The requested provider tool is not allowed")
    tool = catalog[name]
    arguments = _arguments(tool, values)
    try:
        message = await tool.ainvoke(
            {
                "type": "tool_call",
                "name": tool.name,
                "id": uuid4().hex,
                "args": arguments,
            }
        )
    except Exception:
        raise ProviderError(
            "unavailable", "Provider call failed; check connection and permissions"
        ) from None
    if not isinstance(message, ToolMessage) or message.status == "error":
        raise ProviderError("unavailable", "Provider call failed; check connection and permissions")
    artifact = message.artifact
    if isinstance(artifact, dict) and artifact.get("isError") is True:
        raise ProviderError("unavailable", "Provider reported an unsuccessful operation")
    data = artifact.get("structured_content") if isinstance(artifact, dict) else None
    if data is None:
        content = message.content
        text = (
            content
            if isinstance(content, str)
            else "\n".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        )
        try:
            data = json.loads(text)
        except ValueError, TypeError:
            raise ProviderError("unsupported", "Provider returned unstructured content") from None
    if not isinstance(data, dict) or len(json.dumps(data)) > 256 * 1024:
        raise ProviderError("unsupported", "Provider returned an unsupported response shape")
    if data.get("isError") is True:
        raise ProviderError("unavailable", "Provider reported an unsuccessful operation")
    return data


def _external_id(value: str) -> str:
    value = value.strip()
    if value.startswith("https://"):
        url = _provider_url(value)
        parsed = urlsplit(url)
        if not url or "/incidents/" not in parsed.path:
            raise ProviderError("unsupported", "Use an incident.io incident ID or link")
        value = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    if not value or len(value) > 200 or any(char.isspace() for char in value):
        raise ProviderError("unsupported", "Use an incident.io incident ID or link")
    return value


def _provider_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme == "https"
            and parsed.hostname == "app.incident.io"
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
        ):
            return parsed._replace(query="", fragment="").geturl()
    except ValueError:
        pass
    return ""


def _text(value: Any, limit: int = 8000) -> str:
    return service.redact_context(value[:limit]) if isinstance(value, str) else ""


def _snapshot(data: dict[str, Any], expected_id: str | None = None) -> ProviderSnapshot:
    if not isinstance(data, dict):
        raise ProviderError("unsupported", "Provider returned an unsupported incident shape")
    raw = data.get("incident", data)
    if not isinstance(raw, dict):
        raise ProviderError("unsupported", "Provider returned an unsupported incident shape")
    external_id = raw.get("id") or raw.get("incident_id")
    if not isinstance(external_id, str) or (expected_id and external_id != expected_id):
        raise ProviderError("unsupported", "Provider did not return the requested incident ID")

    def value(field: Any) -> ProviderValue:
        return (
            ProviderValue(id=_text(field.get("id")), name=_text(field.get("name")))
            if isinstance(field, dict)
            else ProviderValue()
        )

    url = _provider_url(raw.get("permalink") or raw.get("url"))
    postmortem = raw.get("postmortem")
    if isinstance(postmortem, dict):
        postmortem = postmortem.get("content") or postmortem.get("body") or ""
    return ProviderSnapshot(
        external_id=external_id,
        title=_text(raw.get("name") or raw.get("title")),
        url=url,
        status=value(raw.get("incident_status") or raw.get("status")),
        severity=value(raw.get("severity")),
        slack_channel_id=_text(raw.get("slack_channel_id")),
        postmortem=_text(postmortem),
        resolved_at=_text(raw.get("resolved_at")) or None,
    )


async def _visible(snapshot: ProviderSnapshot) -> bool:
    if not snapshot.slack_channel_id:
        return False
    policy = await service.get_policy()
    if snapshot.slack_channel_id in policy.excluded_channel_ids:
        return False
    try:
        info = await service.slack.channel_info(snapshot.slack_channel_id)
    except Exception:
        return False
    return info.get("id") == snapshot.slack_channel_id and service.slack.channel_allowed(
        info, policy, for_read=True
    )


async def _show(catalog: dict[str, BaseTool], external_id: str) -> ProviderSnapshot:
    snapshot = _snapshot(
        await _invoke(catalog, "incident_show", {"external_id": external_id}), external_id
    )
    if not await _visible(snapshot):
        raise ProviderError(
            "permission_denied", "Provider incident has no verifiable readable Slack channel"
        )
    return snapshot


async def _configuration(catalog: dict[str, BaseTool]) -> dict[str, list[dict[str, str]]]:
    data = await _invoke(catalog, "resource_show", {"resource": "organisation"})
    raw = data.get("organisation", data)
    if not isinstance(raw, dict):
        raise ProviderError(
            "unsupported", "Provider returned unsupported organisation configuration"
        )
    statuses = raw.get("incident_statuses", raw.get("statuses"))
    severities = raw.get("severities")
    if statuses is None and severities is None:
        raise ProviderError(
            "unsupported", "Provider organisation has no supported lifecycle configuration"
        )
    result = {}
    for key, values in (("status_options", statuses), ("severity_options", severities)):
        if values is None:
            result[key] = []
            continue
        if (
            not isinstance(values, list)
            or len(values) > 500
            or any(
                not isinstance(value, dict)
                or not isinstance(value.get("id"), str)
                or not value["id"]
                or not isinstance(value.get("name"), str)
                or not value["name"]
                for value in values
            )
        ):
            raise ProviderError("unsupported", "Provider returned unsupported lifecycle options")
        result[key] = [
            {"id": _text(value["id"], 200), "name": _text(value["name"], 200)} for value in values
        ]
    return result


async def _validate_updates(catalog: dict[str, BaseTool], updates: dict[str, str]) -> None:
    configuration = await _configuration(catalog)
    for key, value in updates.items():
        options = configuration[key.removesuffix("_id") + "_options"]
        if value not in {option["id"] for option in options}:
            raise ProviderError(
                "unsupported",
                "Select a lifecycle value from the provider organisation configuration",
            )


async def _connection_scope(binding: ProviderBinding) -> str:
    connection = await get_workspace_mcp(binding.connection_name)
    if (
        connection is None
        or not connection.enabled
        or connection.url.rstrip("/") != _ENDPOINT
        or "incident_show" not in connection.allowed_tools
    ):
        raise ProviderError(
            "permission_denied", "Incident provider connection is no longer readable"
        )
    return service.fingerprint(
        [
            binding.incident_id,
            binding.workspace_id,
            binding.channel_id,
            binding.provider,
            binding.connection_name,
            binding.external_id,
            connection.transport,
            connection.revision,
            connection.updated_at,
        ]
    )


async def _snapshot_scope(binding: ProviderBinding) -> str:
    scope = await _connection_scope(binding)
    if scope != binding.snapshot_scope:
        raise ProviderError(
            "permission_denied", "Provider connection changed; refresh to verify the saved snapshot"
        )
    return scope


async def _read_snapshot(
    binding: ProviderBinding, catalog: dict[str, BaseTool]
) -> tuple[ProviderSnapshot, str]:
    scope = await _connection_scope(binding)
    snapshot = await _show(catalog, binding.external_id)
    if scope != await _connection_scope(binding):
        raise ProviderError("permission_denied", "Provider connection changed during refresh")
    if snapshot.slack_channel_id != binding.channel_id:
        raise ProviderError(
            "permission_denied", "Provider incident is linked to a different Slack channel"
        )
    return snapshot, scope


async def analysis_scope(record: Incident) -> str:
    """Verify retained provider context without making a provider request."""
    binding = await BINDINGS.get(record.id)
    if binding is None:
        return ""
    policy = await service.get_policy()
    if (
        binding.incident_id != record.id
        or binding.workspace_id != record.workspace_id
        or record.workspace_id != policy.workspace_id
        or binding.channel_id != record.channel_id
        or binding.error_kind == "permission_denied"
        or not binding.snapshot
        or binding.snapshot.external_id != binding.external_id
        or binding.snapshot.slack_channel_id != record.channel_id
    ):
        raise PermissionError("Incident provider binding is no longer readable")
    try:
        scope = await _snapshot_scope(binding)
    except ProviderError as exc:
        raise PermissionError(str(exc)) from None
    if not await _visible(binding.snapshot):
        raise PermissionError("Incident provider Slack access is no longer readable")
    return scope


async def provider_context(incident_id: str) -> dict[str, Any]:
    record = await _accessible(incident_id)
    policy = await service.get_policy()
    binding = await BINDINGS.get(record.id)
    empty = {
        "binding": None,
        "default_connection_name": policy.provider_connection_name,
        "snapshot": None,
        "capabilities": {},
        "last_synced_at": None,
        "error": None,
        "error_kind": None,
        "status_options": [],
        "severity_options": [],
        "configuration_error": None,
        "configuration_error_kind": None,
    }
    if (
        not binding
        or binding.workspace_id != record.workspace_id
        or binding.channel_id != record.channel_id
    ):
        return empty
    if binding.error_kind == "permission_denied":
        return {
            **empty,
            "error": binding.error
            or "Provider incident access was revoked; refresh to verify access",
            "error_kind": "permission_denied",
        }
    try:
        await _snapshot_scope(binding)
        catalog = await _catalog(binding.connection_name)
        connection = await get_workspace_mcp(binding.connection_name)
        assert connection is not None
        capabilities = _capabilities(catalog, connection.allowed_tools)
        if capabilities["read"] != "supported":
            raise ProviderError("unsupported", "Provider incident read schema is unsupported")
        if binding.snapshot and not await _visible(binding.snapshot):
            raise ProviderError(
                "permission_denied", "Provider incident Slack access is unavailable"
            )
    except ProviderError as exc:
        if exc.kind == "unavailable":
            try:
                await _snapshot_scope(binding)
            except ProviderError as scope_error:
                return {**empty, "error": str(scope_error), "error_kind": scope_error.kind}
        if exc.kind == "unavailable" and binding.snapshot and await _visible(binding.snapshot):
            return {
                **empty,
                "binding": binding.model_dump(
                    include={"provider", "connection_name", "external_id", "url"}
                ),
                "snapshot": binding.snapshot.model_dump(),
                "last_synced_at": binding.last_synced_at,
                "capabilities": {
                    "read": "unavailable",
                    "postmortem_write": "unsupported",
                    "status_page_publish": "unsupported",
                },
                "error": str(exc),
                "error_kind": exc.kind,
            }
        return {**empty, "error": str(exc), "error_kind": exc.kind}
    configuration: dict[str, Any] = {
        "status_options": [],
        "severity_options": [],
        "configuration_error": None,
        "configuration_error_kind": None,
    }
    try:
        configuration.update(await _configuration(catalog))
        for kind in ("status", "severity"):
            if not configuration[kind + "_options"]:
                capabilities["update_" + kind] = "unsupported"
    except ProviderError as exc:
        configuration["configuration_error"] = str(exc)
        configuration["configuration_error_kind"] = exc.kind
        for name in ("update_status", "update_severity"):
            if capabilities[name] == "supported":
                capabilities[name] = exc.kind
    try:
        await _snapshot_scope(binding)
    except ProviderError as exc:
        return {**empty, "error": str(exc), "error_kind": exc.kind}
    return {
        **configuration,
        "default_connection_name": policy.provider_connection_name,
        "binding": binding.model_dump(
            include={"provider", "connection_name", "external_id", "url"}
        ),
        "snapshot": binding.snapshot.model_dump() if binding.snapshot else None,
        "capabilities": capabilities,
        "last_synced_at": binding.last_synced_at,
        "error": binding.error,
        "error_kind": binding.error_kind,
    }


async def list_connections(incident_id: str) -> dict[str, Any]:
    await _accessible(incident_id)
    items = []
    for connection in await list_workspace_mcp_records():
        if not connection.enabled or connection.url.rstrip("/") != _ENDPOINT:
            continue
        try:
            catalog = await _catalog(connection.name)
            capabilities = _capabilities(catalog, connection.allowed_tools)
            items.append(
                {
                    "name": connection.name,
                    "capabilities": capabilities,
                    "schemas": {name: _schema(tool) for name, tool in catalog.items()},
                }
            )
        except ProviderError as exc:
            items.append(
                {"name": connection.name, "capabilities": {"read": exc.kind}, "schemas": {}}
            )
    return {"items": items}


async def search_external(incident_id: str, connection_name: str, query: str) -> dict[str, Any]:
    await _accessible(incident_id)
    try:
        data = await _invoke(await _catalog(connection_name), "incident_list", {"query": query})
        raw = data.get("incidents")
        if not isinstance(raw, list):
            raise ProviderError(
                "unsupported", "Provider returned an unsupported incident list shape"
            )
        items = []
        omitted = False
        for item in raw[:100]:
            snapshot = _snapshot(item)
            if await _visible(snapshot):
                items.append(snapshot.model_dump(exclude={"postmortem"}))
            else:
                omitted = True
        return {
            "items": items,
            "external": True,
            "gaps": ["Results without a verifiable readable Slack channel were omitted"]
            if omitted
            else [],
        }
    except ProviderError as exc:
        raise _http(exc) from None


async def read_external(incident_id: str, connection_name: str, external_id: str) -> dict[str, Any]:
    await _accessible(incident_id)
    try:
        snapshot = await _show(await _catalog(connection_name), _external_id(external_id))
        return {"external": True, "incident": snapshot.model_dump()}
    except ProviderError as exc:
        if exc.kind == "permission_denied":
            raise HTTPException(404, "Provider incident is unavailable or not readable") from None
        raise _http(exc) from None


async def submit_provider_command(
    incident_id: str,
    action: str,
    values: dict[str, str],
    request_id: str,
    actor: dict[str, Any],
) -> dict[str, str]:
    record = await _accessible(incident_id)
    operation_id = service.fingerprint([incident_id, "provider", actor.get("id"), request_id])
    payload = {"action": action, **values}
    content_hash = service.fingerprint(payload)
    existing = await service.RECEIPTS.get(operation_id)
    operation = await OPERATIONS.get(operation_id)
    previous = existing or operation
    if previous:
        if previous.content_hash != content_hash:
            raise HTTPException(409, "Request ID was reused with different content")
        return {"command_id": operation_id, "status": "duplicate"}
    try:
        if action == "attach":
            external_id = _external_id(values.get("external_id", ""))
            catalog = await _catalog(values.get("connection_name", ""))
            _arguments(catalog["incident_show"], {"external_id": external_id})
            payload["external_id"] = external_id
        else:
            binding = await BINDINGS.get(incident_id)
            if not binding or binding.workspace_id != record.workspace_id:
                raise HTTPException(409, "Attach an incident provider first")
            payload["connection_name"] = binding.connection_name
            payload["external_id"] = binding.external_id
            catalog = await _catalog(binding.connection_name)
            if action == "status":
                updates = {
                    key: value
                    for key, value in values.items()
                    if key in {"status_id", "severity_id"} and value
                }
                if not updates or len(updates) != len(values):
                    raise HTTPException(422, "Provide only status_id or severity_id")
                if "incident_update" not in catalog:
                    raise ProviderError(
                        "permission_denied", "The incident_update tool is not allowed"
                    )
                _arguments(
                    catalog["incident_update"], {"external_id": binding.external_id, **updates}
                )
                await _validate_updates(catalog, updates)
            elif action != "refresh":
                raise HTTPException(422, "Unsupported provider operation")
    except ProviderError as exc:
        raise _http(exc) from None
    await service.RECEIPTS.put(
        operation_id,
        Receipt(
            id=operation_id,
            workspace_id=record.workspace_id,
            channel_id=record.channel_id,
            kind="provider_command",
            payload=payload,
            actor=actor,
            received_at=time.time(),
            content_hash=content_hash,
        ),
    )
    await service.wake()
    return {"command_id": operation_id, "status": "accepted"}


async def get_operation(incident_id: str, operation_id: str) -> dict[str, Any]:
    record = await _accessible(incident_id)
    operation = await OPERATIONS.get(operation_id)
    if operation and operation.incident_id == incident_id:
        result = operation.model_dump(include={"id", "status", "error", "created_at", "updated_at"})
        if result["status"] == "sending":
            result["status"] = "unknown"
        return result
    receipt = await service.RECEIPTS.get(operation_id)
    if (
        receipt
        and receipt.kind == "provider_command"
        and receipt.workspace_id == record.workspace_id
        and receipt.channel_id == record.channel_id
    ):
        return {"id": operation_id, "status": "accepted", "error": None}
    raise HTTPException(404, "Provider operation not found")


async def refresh_provider(record: Incident) -> None:
    binding = await BINDINGS.get(record.id)
    if not binding:
        return
    try:
        await _accessible(record.id)
        if binding.workspace_id != record.workspace_id or binding.channel_id != record.channel_id:
            raise ProviderError(
                "permission_denied", "Provider binding does not match this incident"
            )
        snapshot, scope = await _read_snapshot(binding, await _catalog(binding.connection_name))
        binding.snapshot = snapshot
        binding.snapshot_scope = scope
        binding.url = snapshot.url
        binding.last_synced_at = now_iso()
        binding.error = None
        binding.error_kind = None
    except (ProviderError, HTTPException) as exc:
        binding.error = (
            str(exc) if isinstance(exc, ProviderError) else "Incident access is unavailable"
        )
        binding.error_kind = exc.kind if isinstance(exc, ProviderError) else "permission_denied"
        logger.info(
            "Incident provider refresh unavailable",
            extra={"incident_id": record.id, "provider_error_kind": binding.error_kind},
        )
    await BINDINGS.put(record.id, binding)
    if not binding.error and binding.snapshot:
        for operation in await OPERATIONS.search_all(filter={"incident_id": record.id}):
            if (
                operation.status in {"unknown", "sending"}
                and operation.connection_name == binding.connection_name
                and operation.external_id == binding.external_id
                and operation.updates
                and all(
                    getattr(binding.snapshot, key.removesuffix("_id")).id == value
                    for key, value in operation.updates.items()
                )
            ):
                operation.status = "succeeded"
                operation.error = None
                operation.updated_at = now_iso()
                await OPERATIONS.put(operation.id, operation)


async def process_provider_receipt(record: Incident, receipt: Receipt) -> bool:
    if receipt.kind != "provider_command":
        return False
    operation = await OPERATIONS.get(receipt.id) or ProviderOperation(
        id=receipt.id,
        incident_id=record.id,
        action=str(receipt.payload.get("action", "")),
        content_hash=receipt.content_hash,
        actor=receipt.actor,
        connection_name=str(receipt.payload.get("connection_name", "")),
        external_id=str(receipt.payload.get("external_id", "")),
        updates={
            key: receipt.payload[key]
            for key in ("status_id", "severity_id")
            if receipt.payload.get(key)
        },
    )
    if operation.status in {"succeeded", "failed"}:
        return True
    try:
        await _accessible(record.id)
        if receipt.workspace_id != record.workspace_id or receipt.channel_id != record.channel_id:
            raise ProviderError("permission_denied", "Provider operation scope changed")
        payload = receipt.payload
        action = operation.action
        binding = await BINDINGS.get(record.id)
        if action != "attach" and (
            not binding
            or binding.connection_name != payload.get("connection_name")
            or binding.external_id != payload.get("external_id")
        ):
            raise ProviderError(
                "permission_denied", "Provider binding changed before operation completed"
            )
        catalog = await _catalog(payload["connection_name"])
        external_id = payload["external_id"]
        if action == "attach":
            binding = ProviderBinding(
                incident_id=record.id,
                workspace_id=record.workspace_id,
                channel_id=record.channel_id,
                connection_name=payload["connection_name"],
                external_id=external_id,
            )
            snapshot, scope = await _read_snapshot(binding, catalog)
            binding.snapshot = snapshot
            binding.snapshot_scope = scope
            binding.url = snapshot.url
            binding.last_synced_at = now_iso()
            await BINDINGS.put(record.id, binding)
        elif action == "status":
            updates = {
                key: payload[key] for key in ("status_id", "severity_id") if payload.get(key)
            }
            if not updates:
                raise ProviderError(
                    "unsupported", "Provider status operation has no explicit change"
                )
            before = await _show(catalog, external_id)
            if before.slack_channel_id != record.channel_id:
                raise ProviderError(
                    "permission_denied", "Provider incident Slack association changed"
                )
            matches = all(
                getattr(before, key.removesuffix("_id")).id == value
                for key, value in updates.items()
            )
            if not matches and operation.status in {"sending", "unknown"}:
                operation.status = "unknown"
                operation.error = "Provider outcome could not be confirmed; no write was retried"
                await OPERATIONS.put(receipt.id, operation)
                return True
            if not matches:
                if "incident_update" not in catalog:
                    raise ProviderError(
                        "permission_denied", "The incident_update tool is not allowed"
                    )
                _arguments(catalog["incident_update"], {"external_id": external_id, **updates})
                await _validate_updates(catalog, updates)
                operation.status = "sending"
                await OPERATIONS.put(receipt.id, operation)
                try:
                    await _invoke(
                        catalog, "incident_update", {"external_id": external_id, **updates}
                    )
                except ProviderError:
                    operation.status = "unknown"
                    operation.error = (
                        "Provider outcome is uncertain; refresh to reconcile before retrying"
                    )
                    await OPERATIONS.put(receipt.id, operation)
                    return True
            await refresh_provider(record)
            current = await BINDINGS.get(record.id)
            if (
                not current
                or current.error
                or not current.snapshot
                or not all(
                    getattr(current.snapshot, key.removesuffix("_id")).id == value
                    for key, value in updates.items()
                )
            ):
                operation.status = "unknown"
                operation.error = "Provider outcome could not be confirmed; no write was retried"
                await OPERATIONS.put(receipt.id, operation)
                return True
        elif action == "refresh":
            await refresh_provider(record)
            current = await BINDINGS.get(record.id)
            if current and current.error:
                raise ProviderError(current.error_kind or "unavailable", current.error)
        else:
            raise ProviderError("unsupported", "Unsupported provider operation")
        operation.status = "succeeded"
        operation.error = None
    except (ProviderError, HTTPException) as exc:
        operation.status = "unknown" if operation.status in {"sending", "unknown"} else "failed"
        operation.error = (
            str(exc) if isinstance(exc, ProviderError) else "Incident access is unavailable"
        )
    operation.updated_at = now_iso()
    await OPERATIONS.put(receipt.id, operation)
    return True

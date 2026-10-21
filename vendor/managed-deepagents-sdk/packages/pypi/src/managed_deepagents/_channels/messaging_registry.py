"""Name → messaging transport registry.

Filled when channels (and connectors that still contribute messaging, e.g.
GitHub) are mounted / collected. ``resolve_runtime_channel`` and schedule
delivery look up transports by file-stem name.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, cast

from managed_deepagents._channels.source_providers import register_declared_source_providers

if TYPE_CHECKING:
    from managed_deepagents._channels.manifest import JsonObject
    from managed_deepagents._channels.runtime import ChannelMessagingAdapter

MessagingFactory = Callable[[Mapping[str, str]], "ChannelMessagingAdapter | None"]

_provider_by_name: dict[str, str] = {}
_provider_config_by_name: dict[str, JsonObject] = {}


def channel_provider_for_name(name: str) -> str | None:
    return _provider_by_name.get(name)


def channel_provider_config_for_name(name: str) -> JsonObject | None:
    return _provider_config_by_name.get(name)


_by_name: dict[str, MessagingFactory] = {}

__all__ = [
    "channel_provider_config_for_name",
    "messaging_transport_for_name",
    "register_channel_messaging",
    "register_connector_messaging",
]


def _register_messaging_sources(sources: Sequence[object]) -> None:
    register_declared_source_providers(sources)
    for source in sources:
        name = getattr(source, "name", None)
        messaging = getattr(source, "messaging", None)
        if not isinstance(name, str) or not name:
            continue

        if callable(messaging):

            def _factory(
                env: Mapping[str, str],
                *,
                _messaging: Callable[..., object] = messaging,
            ) -> ChannelMessagingAdapter | None:
                return cast("ChannelMessagingAdapter | None", _messaging(env))

            _by_name[name] = _factory
        else:
            _by_name.pop(name, None)
        kind = getattr(source, "kind", None)
        if isinstance(kind, str) and kind.strip():
            _provider_by_name[name] = kind
        _provider_config_by_name.pop(name, None)
        requirements = getattr(source, "requirements", None)
        if callable(requirements):
            manifest = requirements(name)
            if isinstance(manifest, Mapping):
                provider = manifest.get("provider")
                if isinstance(provider, str) and provider.strip():
                    _provider_by_name[name] = provider
                config = manifest.get("providerConfig")
                if isinstance(config, Mapping):
                    _provider_config_by_name[name] = cast("JsonObject", config)


def register_channel_messaging(channels: Sequence[object]) -> None:
    """Register messaging factories from collected channels."""
    _register_messaging_sources(channels)


def register_connector_messaging(connectors: Sequence[object]) -> None:
    """Register messaging factories from collected connectors (e.g. GitHub)."""
    _register_messaging_sources(connectors)


def messaging_transport_for_name(
    name: str, env: Mapping[str, str] | None = None
) -> ChannelMessagingAdapter | None:
    """Build a transport for a channel/connector file-stem name."""
    factory = _by_name.get(name)
    if factory is None:
        return None
    return factory(os.environ if env is None else env)

"""The managed channel protocol (Python mirror of the TS contract).

A channel is one inbound messaging surface with one external platform. It may
contribute Events ingress, outbound messaging, and deploy requirements.
Discovery is file-based: the CLI globs modules under ``channels/`` that define a
module-level ``channel`` attribute, imports each one, and hands them to
:func:`collect_channels`, which stamps each with its file-stem ``name``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Literal, TypedDict

__all__ = [
    "CHANNEL_BRAND",
    "Channel",
    "ChannelEventsContext",
    "ChannelRequirements",
    "collect_channels",
    "is_channel",
    "requirements_from_channels",
]

if TYPE_CHECKING:
    from managed_deepagents._channels.manifest import ChannelManifest
    from managed_deepagents._channels.runtime import ChannelMessagingAdapter
else:
    ChannelManifest = Mapping[str, object]
    ChannelMessagingAdapter = object

#: Deploy/runtime requirements a channel declares.
ChannelRequirements = ChannelManifest

#: Attribute marking a value as a managed channel.
CHANNEL_BRAND = "__mda_channel__"

# Keep in sync with CLI ``plugin_module_extensions`` (ts/tsx/mts/cts) plus
# emitted js/mjs and Python sources that may appear in diagnostics paths.
_STEM_RE = re.compile(r"^(?:.*/)?([^/]+)\.(?:ts|tsx|mts|cts|js|mjs|py)$")


class Channel:
    """Base class for managed channels.

    Implement at least one of :meth:`events`, :meth:`messaging`, or
    :meth:`requirements`. A channel that implements none does nothing and is
    rejected by :func:`collect_channels`. Subclasses set a descriptive ``kind``.
    """

    #: Brand inherited by every subclass; do not override.
    __mda_channel__ = True

    #: Diagnostic / provider label (e.g. ``"slack"``).
    #: HTTP mounts use :attr:`name`, not kind.
    kind: str = "channel"

    #: Optional provider-neutral ingress transport discriminator.
    transport: Literal["trigger_server"] | None = None

    #: File-stem instance name, stamped by :func:`collect_channels`.
    name: str | None = None

    #: A PNG or JPEG image in the project (e.g. ``assets/icon.png``) served
    #: publicly at ``/channels/{name}/icon``, for providers that load an app's
    #: icon from a URL.
    icon: str | None = None


class ChannelEventsContext(TypedDict, total=False):
    """Context handed to channel ``events`` for ``/channels/{name}/events``."""

    name: str
    request: object
    raw_body: str | bytes
    channel: Channel


def is_channel(value: object) -> bool:
    """Return ``True`` when ``value`` carries the channel brand."""
    return bool(getattr(value, CHANNEL_BRAND, False))


def _channel_has_hook(channel: object) -> bool:
    return any(
        callable(getattr(channel, hook, None)) for hook in ("events", "messaging", "requirements")
    )


def collect_channels(modules: list[tuple[str, object]]) -> list[Channel]:
    """Validate and collect the channels discovered under ``channels/``.

    Each module should expose exactly one channel via a module-level ``channel``
    attribute. The file stem becomes the instance ``name`` (mount namespace and
    messaging address). Duplicate stems are a hard error. Modules missing that
    attribute, or whose ``channel`` is not a branded channel, are skipped. A
    branded channel that implements no hooks is still a hard error. Duplicate
    channel objects (re-exported from two modules) are de-duplicated by identity.

    ``modules`` is a list of ``(source, module)`` pairs where ``source`` is the
    project-relative path used in error messages.
    """
    collected: list[Channel] = []
    seen_objects: set[int] = set()
    seen_names: set[str] = set()

    for source, module in modules:
        candidate = getattr(module, "channel", None)
        if candidate is None or not is_channel(candidate):
            continue
        if not _channel_has_hook(candidate):
            raise ValueError(
                f"{source} channel (kind {candidate.kind!r}) implements none of "
                f"`events`, `messaging`, or `requirements` and would do nothing."
            )
        name = _channel_stem_name(source)
        if name in seen_names:
            raise ValueError(f"duplicate channel name '{name}' from channels/")
        seen_names.add(name)
        if id(candidate) in seen_objects:
            continue
        seen_objects.add(id(candidate))
        object.__setattr__(candidate, "name", name)
        collected.append(candidate)

    from managed_deepagents._channels.source_providers import (  # noqa: PLC0415
        register_declared_source_providers,
    )

    register_declared_source_providers(collected)
    return collected


def _channel_stem_name(source: str) -> str:
    match = _STEM_RE.match(source.replace("\\", "/"))
    if match is None:
        raise ValueError(f'invalid channel path "{source}"')
    base = match.group(1)
    if not base or base == "channel":
        raise ValueError(
            f'invalid channel path "{source}": use channels/<name>.py (not channel.py)'
        )
    return base


def requirements_from_channels(
    channels: Sequence[object],
) -> dict[str, ChannelRequirements]:
    """Build the requirements map (file-stem → manifest) from ``requirements`` hooks."""
    out: dict[str, ChannelRequirements] = {}
    for channel in channels:
        hook = getattr(channel, "requirements", None)
        name = getattr(channel, "name", None)
        if not callable(hook) or not isinstance(name, str) or not name:
            continue
        out[name] = hook(name)
    return out

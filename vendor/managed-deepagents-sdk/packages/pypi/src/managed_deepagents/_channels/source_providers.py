"""Trusted source providers from declarations and built-in ingress."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, cast

_BUILTIN_SOURCE_PROVIDERS = frozenset({"slack", "github", "linear"})
_declared_providers: set[str] = set()


class _RequirementsHook(Protocol):
    def __call__(self, name: str, /) -> object: ...


def register_declared_source_providers(sources: Sequence[object]) -> None:
    """Register named channel and connector providers, including receive-only sources."""
    providers: set[str] = set()
    for source in sources:
        name = getattr(source, "name", None)
        if not isinstance(name, str) or not name:
            continue
        kind = getattr(source, "kind", None)
        if isinstance(kind, str) and kind.strip():
            providers.add(kind)
        requirements = getattr(source, "requirements", None)
        if callable(requirements):
            manifest = cast("_RequirementsHook", requirements)(name)
            if isinstance(manifest, Mapping):
                provider = manifest.get("provider")
                if isinstance(provider, str) and provider.strip():
                    providers.add(provider)
    _declared_providers.update(providers)


def is_trusted_source_provider(provider: str) -> bool:
    """Use one provider policy for auth and runtime identity."""
    return provider in _BUILTIN_SOURCE_PROVIDERS or provider in _declared_providers

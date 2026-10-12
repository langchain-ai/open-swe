"""Behavior when an optional sandbox provider's dependency group isn't installed."""

import importlib

import pytest

from openswe.sandboxes.providers import registry
from openswe.sandboxes.providers.registry import (
    _load_sandbox_factory,
)

SDK_MODULES = {
    "daytona": "daytona",
    "modal": "modal",
    "runloop": "runloop_api_client",
    "e2b": "e2b",
}


@pytest.mark.parametrize("sandbox_type", ["daytona", "modal", "runloop", "e2b"])
def test_missing_provider_extra_raises_install_hint(sandbox_type, monkeypatch):
    error = ModuleNotFoundError(
        f"No module named {SDK_MODULES[sandbox_type]!r}", name=SDK_MODULES[sandbox_type]
    )

    def _raise(name, *args, **kwargs):
        if name.endswith(sandbox_type):
            raise error
        return importlib.import_module(name)

    monkeypatch.setattr(registry, "import_module", _raise)
    with pytest.raises(ValueError, match=f"sandbox-{sandbox_type}"):
        _load_sandbox_factory(sandbox_type)


def test_unrelated_module_error_propagates(monkeypatch):
    def _raise(name, *args, **kwargs):
        raise ModuleNotFoundError("No module named 'unrelated'", name="unrelated")

    monkeypatch.setattr(registry, "import_module", _raise)
    with pytest.raises(ModuleNotFoundError):
        _load_sandbox_factory("e2b")

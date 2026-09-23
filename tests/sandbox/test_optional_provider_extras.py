"""Behavior when an optional sandbox provider's dependency group isn't installed."""

import importlib
import sys
from unittest import mock

import pytest

from agent.sandboxes.providers import registry
from agent.sandboxes.providers.registry import (
    _load_sandbox_factory,
    validate_sandbox_startup_config,
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


@pytest.mark.parametrize("sandbox_type", ["daytona", "modal", "runloop", "e2b"])
def test_missing_provider_extra_fails_startup_validation(sandbox_type, monkeypatch):
    monkeypatch.setenv("SANDBOX_TYPE", sandbox_type)
    sdk = SDK_MODULES[sandbox_type]
    error = ModuleNotFoundError(f"No module named {sdk!r}", name=sdk)

    def _raise(name, *args, **kwargs):
        raise error

    monkeypatch.setattr(registry, "import_module", _raise)
    with pytest.raises(ValueError, match="dependency group"):
        validate_sandbox_startup_config()


def test_unrelated_module_error_propagates(monkeypatch):
    def _raise(name, *args, **kwargs):
        raise ModuleNotFoundError("No module named 'unrelated'", name="unrelated")

    monkeypatch.setattr(registry, "import_module", _raise)
    with pytest.raises(ModuleNotFoundError):
        _load_sandbox_factory("e2b")


def test_provider_loads_when_sdk_installed(monkeypatch):
    # With extras installed (or faked), factory loading must not raise.
    fake_sdk = mock.MagicMock()
    fake_wrapper = mock.MagicMock()
    fake_module = mock.MagicMock(create_e2b_sandbox=lambda *a, **k: fake_wrapper)

    real_import = importlib.import_module

    def _import(name, *args, **kwargs):
        if name == "e2b":
            return fake_sdk
        if name == "agent.sandboxes.providers.e2b":
            return fake_module
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(registry, "import_module", _import)
    assert _load_sandbox_factory("e2b") is fake_module.create_e2b_sandbox
    assert sys.modules.get("e2b") is not fake_sdk  # nothing was cached globally

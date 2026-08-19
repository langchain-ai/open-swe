"""Open SWE forces the queue onto one event loop per process."""

import os
import sys
import types

import pytest

from agent.utils.event_loop import ISOLATED_LOOPS_ENV, pin_single_event_loop


def test_pin_clears_the_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ISOLATED_LOOPS_ENV, "true")
    monkeypatch.delitem(sys.modules, "langgraph_api.config", raising=False)

    pin_single_event_loop()

    # The server reads its config from the environment at import time.
    assert os.environ[ISOLATED_LOOPS_ENV] == "false"


def test_pin_overrides_config_the_server_already_read(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("langgraph_api.config")
    module.BG_JOB_ISOLATED_LOOPS = True
    monkeypatch.setitem(sys.modules, "langgraph_api.config", module)
    monkeypatch.setenv(ISOLATED_LOOPS_ENV, "true")

    pin_single_event_loop()

    assert module.BG_JOB_ISOLATED_LOOPS is False


def test_pin_fails_when_the_setting_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    """A renamed setting means the loop model changed and must be re-verified."""
    module = types.ModuleType("langgraph_api.config")
    monkeypatch.setitem(sys.modules, "langgraph_api.config", module)

    with pytest.raises(RuntimeError, match="one event loop per process"):
        pin_single_event_loop()

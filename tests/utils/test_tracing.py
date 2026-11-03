"""Runs are read from the project the MDA deployment traces them into."""

import pytest

from openswe.utils.tracing import tracing_project


def test_the_mda_deployments_project_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_PROJECT", "backend")
    monkeypatch.setenv("MDA_LANGSMITH_PROJECT", "open-swe-mda")

    assert tracing_project() == "open-swe-mda"


def test_unset_it_is_this_backends_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_PROJECT", "backend")
    monkeypatch.delenv("MDA_LANGSMITH_PROJECT", raising=False)

    assert tracing_project() == "backend"

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from agent.utils.deferred_model import DeferredErrorModel, make_deferred_error_model


def test_deferred_error_model_binds_tools_without_raising() -> None:
    model = DeferredErrorModel(error_message="missing key", model_id="openai:gpt-5")

    assert model.bind_tools([]) is model
    assert model._get_ls_params()["ls_model_name"] == "openai:gpt-5"


def test_deferred_error_model_raises_on_first_call() -> None:
    model = make_deferred_error_model(RuntimeError("missing key"), model_id="openai:gpt-5")

    with pytest.raises(ValueError, match="missing key"):
        model.invoke([HumanMessage(content="hello")])

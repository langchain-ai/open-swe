"""LangGraph entrypoint for the desktop E2E's scripted model."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake_llm import FakeScriptedChatModel, build_script  # noqa: E402

from agent import server  # noqa: E402


def _fake_make_model(model_id: str, **kwargs: object):  # noqa: ARG001
    return FakeScriptedChatModel(script=build_script())


server.make_model = _fake_make_model
traced_agent = server.traced_agent

__all__ = ["traced_agent"]

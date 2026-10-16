"""LangGraph graph entrypoint for the E2E Managed Deep Agents server.

Serves the graph ``mda build`` compiled from the real ``mda/`` project, with the
model it builds swapped for the scripted fake. It runs in the ``mda/`` project's
environment, which has no ``openswe``: the backend's hooks, tools and sandbox
are the E2E harness's own, reached over HTTP like a deployed runtime does.
"""

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if not os.environ.get("E2E_REAL_LLM"):
    # From the build directory langgraph dev serves. Every model the runtime builds,
    # the run's own and each call's override from the backend, is built here.
    from e2e_env import BASE_URL
    from fake_llm import LAST_SYSTEM_PROMPT, FakeScriptedChatModel, build_script
    from langchain_core.language_models import BaseChatModel
    from rt import models

    LAST_SYSTEM_PROMPT.report_to = f"{BASE_URL}/control/last-system-prompt"

    def _fake_model(_model_id: str, _kwargs_json: str, _use_gateway: bool) -> BaseChatModel:
        return FakeScriptedChatModel(script=build_script())

    models._build = _fake_model

if os.environ.get("E2E_EXIT_WHEN_ORPHANED"):
    # Playwright closes the webServer stdin pipe when its runner exits, as harness.py relies on.
    def _exit_when_orphaned() -> None:
        try:
            sys.stdin.buffer.read()
        except Exception:
            return
        os._exit(0)

    threading.Thread(target=_exit_when_orphaned, daemon=True).start()

from _mda_entry import agent  # noqa: E402

__all__ = ["agent"]

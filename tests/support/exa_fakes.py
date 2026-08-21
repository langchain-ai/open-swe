"""A stand-in for the ``exa_py`` module.

``web_search`` imports ``exa_py`` lazily and constructs ``Exa(api_key)`` itself,
so tests swap the whole module in via ``monkeypatch.setitem(sys.modules, ...)``.
"""

from types import SimpleNamespace
from typing import Any


def fake_exa_module(result: str) -> SimpleNamespace:
    """An ``exa_py`` whose ``Exa`` answers every search with ``result``."""

    class Exa:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

        def search_and_contents(self, *args: Any, **kwargs: Any) -> str:
            return result

        def search(self, *args: Any, **kwargs: Any) -> str:
            return result

    return SimpleNamespace(Exa=Exa)

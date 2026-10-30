"""``mda/rt/input_messages.py`` writes the envelope byte for byte as the backend does.

MDA's channel handlers keep their own copy because ``mda/`` never imports the backend
(AGENTS.md). The model and the backend's context dedupe read the text, so the copy
must not drift; it is standard library only so this test can load it.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from openswe import input_messages


def _mda_copy() -> ModuleType:
    path = Path(__file__).parents[2] / "mda" / "rt" / "input_messages.py"
    spec = importlib.util.spec_from_file_location("mda_input_messages", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MDA = _mda_copy()
_DATA = {
    "issue": {
        "id": "issue-1",
        "identifier": "ENG-1",
        "title": 'Fix <the> "login" & more\nsecond line',
        "url": None,
    },
    "comment_id": "comment-1",
}


def test_the_system_introduction_matches() -> None:
    system = {"id": "system:linear-issue", "display_name": "Linear <issue>", "platform": "linear"}
    assert _MDA.system_introduction(system) == input_messages.system_introduction(system)


@pytest.mark.parametrize(
    "content",
    [
        "Fix it in repo:acme/api <now> & \"then\" 'test'",
        [
            {"type": "text", "text": "The issue\nwith lines"},
            {"type": "image", "base64": "aGk=", "mime_type": "image/png"},
        ],
        [{"type": "image", "base64": "aGk=", "mime_type": "image/png"}],
    ],
    ids=["text", "text-and-image", "image-only"],
)
@pytest.mark.parametrize("kind", ["human", "system"])
def test_an_input_message_matches(content: object, kind: str) -> None:
    context = {"sender_id": "linear:user-1", "surface": "linear", "kind": kind, "data": _DATA}
    write = "human_input" if kind == "human" else "system_input"
    assert getattr(_MDA, write)(content, context) == getattr(input_messages, write)(
        content, context
    )

"""Guardrail: user-role messages only ever leave the app inside an envelope.

An unenveloped message is unparseable in the transcript, so the UI falls back to
rendering the raw text — platform metadata and all — and its `<>` go unescaped.
"""

import ast
import pathlib

AGENT_ROOT = pathlib.Path(__file__).resolve().parents[2] / "agent"

_SERIALIZER_MODULES = {"input_messages.py"}


def _role_user_literals(tree: ast.AST) -> list[int]:
    found: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if (
                isinstance(key, ast.Constant)
                and key.value == "role"
                and isinstance(value, ast.Constant)
                and value.value == "user"
            ):
                found.append(node.lineno)
    return found


def test_no_module_hand_rolls_a_user_message() -> None:
    offenders: dict[str, list[int]] = {}
    for path in AGENT_ROOT.rglob("*.py"):
        relative = path.relative_to(AGENT_ROOT).as_posix()
        if relative in _SERIALIZER_MODULES:
            continue
        lines = _role_user_literals(ast.parse(path.read_text()))
        if lines:
            offenders[relative] = lines
    assert not offenders, (
        f"build these through agent.input_messages.human_input/system_input instead: {offenders}"
    )

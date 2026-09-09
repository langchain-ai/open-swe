import logging
from pathlib import Path

import pytest

from agent import prompt as prompt_mod

# U+00CD encodes to C3 8D in UTF-8, and 0x8D is undefined in cp1252 — the byte the
# original report failed on. Reading this with the Windows locale encoding raises
# rather than quietly producing mojibake, so it pins the encoding rather than the
# happy path.
_NON_CP1252_TEXT = "Prefer ÍSO dates — always."


def test_default_prompt_path_is_read_as_utf8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompt_file = tmp_path / "org-prompt.md"
    prompt_file.write_text(_NON_CP1252_TEXT, encoding="utf-8")
    monkeypatch.setattr(prompt_mod, "DEFAULT_PROMPT_PATH", str(prompt_file))

    section = prompt_mod._load_default_prompt()

    assert _NON_CP1252_TEXT in section
    assert "### Custom Instructions" in section


def test_unreadable_default_prompt_path_logs_the_cause_and_keeps_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(prompt_mod, "DEFAULT_PROMPT_PATH", str(tmp_path / "absent.md"))

    with caplog.at_level(logging.ERROR, logger=prompt_mod.logger.name):
        assert prompt_mod._load_default_prompt() == ""

    assert "Failed to read default prompt" in caplog.text
    # The traceback is what tells an operator why their file was skipped.
    assert "FileNotFoundError" in caplog.text

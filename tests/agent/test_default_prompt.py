from pathlib import Path

import pytest

from agent import prompt


def test_custom_default_prompt_is_read_as_utf8(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_bytes("Use an em dash â€” safely.".encode())
    monkeypatch.setattr(prompt, "DEFAULT_PROMPT_PATH", str(prompt_path))

    assert "Use an em dash â€” safely." in prompt._load_default_prompt()

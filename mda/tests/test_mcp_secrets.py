import json
import shutil
import stat
from pathlib import Path

import pytest
from deepagents.backends.local_shell import LocalShellBackend
from runtime_api.mcp_secrets import McpResult


async def test_session_credentials_are_saved_privately_not_returned(tmp_path: Path) -> None:
    access = "eyJ" + "a" * 24 + "." + "b" * 24 + "." + "c" * 16
    refresh = "synthetic-refresh-credential"
    session = {
        "host": "beta.smith.langchain.com",
        "storage_key": "sb-example-auth-token",
        "session": {"access_token": access, "refresh_token": refresh},
    }
    content = [{"type": "text", "text": json.dumps(session)}, {"text": access}]
    artifact = {"structured_content": session, "duplicate": refresh}
    result = McpResult(content, artifact)
    with pytest.raises(ValueError, match="without a sandbox"):
        await result.protect(None)
    protected, saved_artifact = await result.protect(
        LocalShellBackend(root_dir=tmp_path, virtual_mode=False)
    )
    returned = json.dumps([protected, saved_artifact])
    assert access not in returned and refresh not in returned
    assert "beta.smith.langchain.com" in returned
    assert isinstance(protected, list) and isinstance(protected[-1], dict)
    text = protected[-1]["text"]
    assert isinstance(text, str)
    path = Path(json.loads(text)["session_file"])
    try:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        original = json.loads(path.read_text())
        assert original == {"content": content, "artifact": artifact}
        assert McpResult(original["content"], original["artifact"]).session() == session
    finally:
        shutil.rmtree(path.parent)
    assert await McpResult("ordinary result", None).protect(None) == ("ordinary result", None)

import json

from runtime_api.mcp_secrets import McpResult, Sessions


def test_session_credentials_are_held_by_handle_not_returned() -> None:
    access = "eyJ" + "a" * 24 + "." + "b" * 24 + "." + "c" * 16
    refresh = "synthetic-refresh-credential"
    session = {
        "host": "beta.smith.langchain.com",
        "storage_key": "sb-example-auth-token",
        "session": {"access_token": access, "refresh_token": refresh},
    }
    content = [{"type": "text", "text": json.dumps(session)}, {"text": access}]
    artifact = {"structured_content": session, "duplicate": refresh}
    held: Sessions = {}
    returned, returned_artifact = McpResult(content, artifact).withheld(held)
    visible = json.dumps([returned, returned_artifact])
    assert access not in visible and refresh not in visible
    assert isinstance(returned, list) and isinstance(returned[-1], dict)
    handle = json.loads(returned[-1]["text"])["session_handle"]
    assert held == {handle: session}
    assert McpResult(content, None).withheld(None)[0] != content
    ordinary = [{"type": "text", "text": '{"access_token": null, "note": "café is null"}'}]
    assert McpResult(ordinary, None).withheld({}) == (ordinary, None)

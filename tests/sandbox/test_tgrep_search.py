import json

from agent.sandboxes.tgrep_search import build_tgrep_command, parse_tgrep_result


def test_tgrep_command_does_not_interpolate_inputs() -> None:
    command = build_tgrep_command("$(touch /tmp/pwned)", "/workspace/repo", "*.py'; exit 1")

    assert "touch /tmp/pwned" not in command
    assert "*.py'; exit 1" not in command


def test_parse_tgrep_result() -> None:
    result = parse_tgrep_result(
        json.dumps(
            {
                "status": "ok",
                "matches": [{"path": "/workspace/repo/a.py", "line": 2, "text": "needle"}],
                "truncated": True,
            }
        )
    )

    assert result is not None
    assert result.matches == [{"path": "/workspace/repo/a.py", "line": 2, "text": "needle"}]
    assert result.truncated is True


def test_parse_tgrep_result_falls_back_on_unavailable_or_malformed_output() -> None:
    assert parse_tgrep_result('{"status":"unavailable"}') is None
    assert parse_tgrep_result("not json") is None

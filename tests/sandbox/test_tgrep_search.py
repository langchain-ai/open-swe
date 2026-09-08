from agent.sandboxes.tgrep_search import build_tgrep_command, parse_tgrep_result

_START = "__OPEN_SWE_TGREP_START__"
_END = "__OPEN_SWE_TGREP_END__"


def test_tgrep_command_shell_quotes_inputs() -> None:
    command = build_tgrep_command("$(touch /tmp/pwned)", "/workspace/repo", "*.py'; exit 1")

    assert "'$(touch /tmp/pwned)'" in command
    assert "'*.py'\"'\"'; exit 1'" in command


def test_parse_tgrep_result() -> None:
    match = "/workspace/repo/a.py\0:2:needle"

    result = parse_tgrep_result(f"{_START}\n{match}\n{match}\n{_END}", 1)

    assert result is not None
    assert result.matches == [{"path": "/workspace/repo/a.py", "line": 2, "text": "needle"}]
    assert result.truncated is True


def test_parse_tgrep_result_falls_back_on_incomplete_or_malformed_output() -> None:
    assert parse_tgrep_result(f"{_START}\n") is None
    assert parse_tgrep_result(f"{_START}\nnot a match\n{_END}") is None

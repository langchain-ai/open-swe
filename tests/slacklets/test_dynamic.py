import pytest

from openswe.slacklets.dynamic import ProgramCommand, SlackletDefinition, SlackletProgramError

_CHECKLIST = """
function init(params) { return { items: params.items, done: [] }; }
function apply(state, command) {
  if (command.op !== "check") throw new Error("unknown op " + command.op);
  return { ...state, done: [...state.done, command.params.item] };
}
function render(state) {
  return {
    text: state.done.length + "/" + state.items.length + " done",
    blocks: [{ type: "section", text: { type: "mrkdwn", text: state.done.join(", ") || "none" } }],
  };
}
"""


def _kind(source: str = _CHECKLIST):
    return SlackletDefinition(name="Checklist", source=source, source_sha256="").kind()


def test_commands_run_through_the_agents_program_and_replay_to_the_same_state() -> None:
    kind = _kind()
    started = kind.apply(None, ProgramCommand(op="init", params={"items": ["a", "b"]}))
    checked = kind.apply(started, ProgramCommand(op="check", params={"item": "a"}))

    assert checked.fallback_text() == "1/2 done"
    assert checked.data == {"items": ["a", "b"], "done": ["a"]}
    with pytest.raises(SlackletProgramError, match="unknown op"):
        kind.apply(checked, ProgramCommand(op="uncheck"))


@pytest.mark.parametrize(
    "body",
    [
        "Date.now()",
        "Math.random()",
        "fetch('https://example.com')",
        "require('fs')",
        "(() => { while (true) {} })()",
    ],
)
def test_programs_get_no_clock_randomness_io_or_unbounded_time(body: str) -> None:
    source = _CHECKLIST.replace(
        "return { items: params.items, done: [] };", f"{body}; return {{}};"
    )

    with pytest.raises(SlackletProgramError):
        _kind(source).apply(None, ProgramCommand(op="init"))


def test_render_may_only_use_blocks_every_surface_draws() -> None:
    source = _CHECKLIST.replace('type: "section", text:', 'type: "actions", elements: [], text:')

    with pytest.raises(SlackletProgramError, match="section, context and divider"):
        _kind(source).apply(None, ProgramCommand(op="init", params={"items": []}))

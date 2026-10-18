import pytest

from openswe.slacklets.pull_request import (
    Attach,
    Ci,
    PullRequestView,
    SetCi,
    SetHead,
    StaleCommand,
    apply,
)


def _view(**changes: object) -> PullRequestView:
    base = PullRequestView(
        url="https://github.com/acme/app/pull/7",
        repo="acme/app",
        number=7,
        title="Add greet",
        status="draft",
        ci=Ci(sha="a" * 40, status="pending"),
    )
    return base.model_copy(update=changes)


def test_replaying_the_commands_reaches_what_github_reported() -> None:
    attached = _view()
    observed = _view(
        status="open",
        ci=Ci(sha="b" * 40, status="failing", failed=("lint",)),
        reviews={"bob": "changes_requested"},
    )

    commands = attached.commands_to(observed)

    assert [command.op for command in commands] == [
        "set_status",
        "set_head",
        "set_ci",
        "set_review",
    ]
    replayed = apply(None, Attach(view=attached))
    for command in commands:
        replayed = apply(replayed, command)
    assert replayed == observed
    assert observed.commands_to(observed) == []


def test_a_new_head_restarts_ci_without_a_ci_command_while_it_runs() -> None:
    observed = _view(ci=Ci(sha="b" * 40, status="pending"))

    assert _view().commands_to(observed) == [SetHead(sha="b" * 40)]


def test_ci_for_a_commit_the_slacklet_moved_past_is_refused() -> None:
    moved = apply(_view(), SetHead(sha="b" * 40))

    with pytest.raises(StaleCommand):
        apply(moved, SetCi(sha="a" * 40, status="passing"))

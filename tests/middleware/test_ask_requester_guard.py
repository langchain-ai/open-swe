"""Which pull requests an Ask run's push records its requester on."""

from types import SimpleNamespace

import pytest

from openswe.middleware.ask_requester_guard import AskRequesterGuardMiddleware


def _request(command: str) -> SimpleNamespace:
    return SimpleNamespace(tool_call={"name": "execute", "args": {"command": command}, "id": "1"})


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        pytest.param("git push origin robin/a", ["robin/a"], id="named-branch"),
        pytest.param("git push origin HEAD:refs/heads/robin/b", ["robin/b"], id="refspec"),
        pytest.param("git push", ["robin/a", "robin/b"], id="no-branch-records-all"),
        pytest.param("git push origin 'unbalanced", ["robin/a", "robin/b"], id="unparsable"),
        pytest.param(
            "git push origin robin/a && git push -u origin HEAD",
            ["robin/a", "robin/b"],
            id="an-implicit-push-alongside-a-named-one",
        ),
        pytest.param(
            "git push origin robin/a; git push origin robin/b",
            ["robin/a", "robin/b"],
            id="two-named-pushes",
        ),
    ],
)
def test_a_push_records_its_requester_on_the_branch_it_names(
    command: str, expected: list[str]
) -> None:
    pulls = [SimpleNamespace(head_ref="robin/a"), SimpleNamespace(head_ref="robin/b")]
    picked = AskRequesterGuardMiddleware._pushed(_request(command), pulls)  # type: ignore[arg-type]
    assert [pull.head_ref for pull in picked] == expected


def test_a_push_records_on_every_pull_request_sharing_the_branch_name() -> None:
    pulls = [
        SimpleNamespace(head_ref="robin/a", repo="one"),
        SimpleNamespace(head_ref="robin/a", repo="two"),
        SimpleNamespace(head_ref="robin/b", repo="one"),
    ]
    picked = AskRequesterGuardMiddleware._pushed(_request("git push origin robin/a"), pulls)  # type: ignore[arg-type]
    assert [pull.repo for pull in picked] == ["one", "two"]

from agent.github.squash_message import (
    CommitParent,
    GitCommit,
    GitHubUser,
    GitPerson,
    PullRequestCommit,
    SquashSource,
)

_RAMON = GitPerson(name="Ramon", email="ramon@users.noreply.github.com")
_BOT = "Co-authored-by: open-swe[bot] <open-swe@users.noreply.github.com>"


def _commit(
    message: str,
    author: GitPerson = _RAMON,
    login: str | None = "ramon",
    parents: int = 1,
) -> PullRequestCommit:
    return PullRequestCommit(
        commit=GitCommit(message=message, author=author),
        author=GitHubUser(login=login) if login else None,
        parents=[CommitParent(sha=str(i)) for i in range(parents)],
    )


def test_squash_message_lists_each_commit_once_after_the_description() -> None:
    source = SquashSource(
        title="feat: kitchen mode",
        body="Opt Slack channels into kitchen mode.\n\n- require channel opt-in\n",
        commits=[
            _commit(f"feat: kitchen mode\n\n{_BOT}"),
            _commit(f"feat: accept untagged messages\n\nWhy it matters.\n\n{_BOT}"),
            _commit(f"require channel opt-in\n\n{_BOT}"),
            _commit("Merge branch 'main' into kitchen", parents=2),
            _commit(f"feat: accept untagged messages\n\n{_BOT}"),
            _commit(
                "fix: typo",
                author=GitPerson(name="Ada", email="ada@example.com"),
                login="ada",
            ),
        ],
    )

    assert source.message("Ramon") == (
        "Opt Slack channels into kitchen mode.\n\n- require channel opt-in\n\n"
        "* feat: accept untagged messages\n"
        "* fix: typo\n\n"
        f"{_BOT}\n"
        "Co-authored-by: Ada <ada@example.com>"
    )


def test_squash_message_moves_body_co_authors_into_the_trailer_block() -> None:
    source = SquashSource(
        title="fix: typo",
        body="Fix it.\n\nCo-authored-by: Ada <ada@example.com>",
        commits=[
            _commit(
                "fix: spelling", author=GitPerson(name="A", email="ADA@example.com"), login="ada"
            )
        ],
    )

    assert source.message("ramon") == (
        "Fix it.\n\n* fix: spelling\n\nCo-authored-by: Ada <ada@example.com>"
    )


def test_squash_message_keeps_the_author_when_the_merger_is_someone_else() -> None:
    source = SquashSource(title="fix: typo", body=None, commits=[_commit("fix: typo")])

    assert source.message() == "Co-authored-by: Ramon <ramon@users.noreply.github.com>"

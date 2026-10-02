from pytest import MonkeyPatch

from agent.github.repo_files import RepoSettings


def test_review_channel_most_files_and_ties(monkeypatch: MonkeyPatch) -> None:
    settings = RepoSettings.model_validate(
        {
            "reviewChannel": "#fallback",
            "reviewChannelRules": [
                {"paths": ["ui/*"], "channel": "#frontend"},
                {"paths": ["agent/*"], "channel": "#backend"},
            ],
        }
    )
    assert settings.channel_for_files(["ui/a", "ui/nested/b", "agent/c"]) == "#frontend"
    choices: list[str] = []

    def choose(channels: list[str]) -> str:
        choices.extend(channels)
        return channels[-1]

    monkeypatch.setattr("agent.github.repo_files.random.choice", choose)
    assert settings.channel_for_files(["ui/a", "agent/b", "README.md"]) == "#backend"
    assert set(choices) == {"#frontend", "#backend"}
    choices.clear()
    assert settings.channel_for_files(["ui/a", "ui/b", "agent/c", "README.md"]) == "#frontend"
    assert choices == ["#frontend"]
    assert settings.channel_for_files(["README.md"]) == ""
    assert RepoSettings(reviewChannel="#fallback").channel_for_files(["README.md"]) == "#fallback"

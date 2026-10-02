from pytest import MonkeyPatch

from agent.github.repo_files import RepoSettings


def test_review_channel_majority_and_no_majority(monkeypatch: MonkeyPatch) -> None:
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
    assert settings.channel_for_files(["ui/a", "agent/b", "README.md"]) == "#fallback"
    assert set(choices) == {"#frontend", "#backend", "#fallback"}
    assert settings.channel_for_files(["README.md"]) == "#fallback"

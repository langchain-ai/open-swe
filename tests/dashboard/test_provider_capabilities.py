from agent.dashboard.provider_capabilities import provider_capabilities_payload


def test_provider_capabilities_exposes_only_bundled_metadata() -> None:
    payload = provider_capabilities_payload()

    assert payload["models"]
    assert payload["skills"] == [
        {
            "name": "baby-sit",
            "description": (
                "Monitor a GitHub pull request until CI is green, diagnose failures, and rerun "
                "only evidence-backed flaky GitHub Actions jobs."
            ),
            "path": "/bundled-skills/baby-sit/SKILL.md",
            "scope": "bundled",
            "enabled": True,
        }
    ]
    assert payload["slash_commands"] == [
        {
            "name": "baby-sit",
            "description": payload["skills"][0]["description"],
        }
    ]
    assert "instructions" not in payload["skills"][0]

from agent.expedited_review.eligibility import (
    MAX_CHANGED_LINES,
    ChangedFile,
    EligibleDiff,
    Ineligible,
    assess_eligibility,
)


def _file(
    name: str, *, additions: int = 1, deletions: int = 0, patch: str | None = "@@"
) -> ChangedFile:
    return ChangedFile(filename=name, additions=additions, deletions=deletions, patch=patch)


def test_small_text_change_is_eligible_with_a_stable_fingerprint() -> None:
    files = [_file("src/app.py", additions=3, deletions=2, patch="@@ -1 +1 @@\n-a\n+b")]

    first = assess_eligibility(files)
    second = assess_eligibility(list(reversed(files)))

    assert isinstance(first, EligibleDiff) and isinstance(second, EligibleDiff)
    assert first.changed_lines == 5
    assert first.fingerprint == second.fingerprint


def test_fingerprint_changes_with_the_patch() -> None:
    before = assess_eligibility([_file("src/app.py", patch="+a")])
    after = assess_eligibility([_file("src/app.py", patch="+b")])

    assert isinstance(before, EligibleDiff) and isinstance(after, EligibleDiff)
    assert before.fingerprint != after.fingerprint


def test_line_cap_is_inclusive() -> None:
    at_cap = assess_eligibility([_file("a.py", additions=MAX_CHANGED_LINES)])
    over_cap = assess_eligibility([_file("a.py", additions=MAX_CHANGED_LINES + 1)])

    assert isinstance(at_cap, EligibleDiff)
    assert isinstance(over_cap, Ineligible)


def test_files_without_a_text_patch_are_refused() -> None:
    verdict = assess_eligibility([_file("logo.png", patch=None)])

    assert isinstance(verdict, Ineligible)
    assert "logo.png" in verdict.reason


def test_protected_paths_are_refused_wherever_they_appear() -> None:
    for path in (
        ".github/workflows/ci.yml",
        "agent/auth/session.py",
        "config/secrets.yaml",
        "agent/database/migrations/versions/0012_x.py",
        "ui/package.json",
        "requirements-dev.txt",
        "deploy/.env.production",
        "certs/server.pem",
        "src/oauth_client.ts",
    ):
        verdict = assess_eligibility([_file("README.md"), _file(path)])
        assert isinstance(verdict, Ineligible), path


def test_renames_out_of_protected_paths_are_refused() -> None:
    moved = ChangedFile(
        filename="agent/session.py",
        previous_filename="agent/auth/session.py",
        additions=1,
        patch="+x",
    )

    assert isinstance(assess_eligibility([moved]), Ineligible)


def test_ordinary_paths_with_protected_words_inside_identifiers_pass() -> None:
    verdict = assess_eligibility([_file("agent/authoring_tools.py"), _file("docs/tokenizer.md")])

    assert isinstance(verdict, EligibleDiff)

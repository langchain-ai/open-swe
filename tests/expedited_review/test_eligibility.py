from agent.expedited_review.eligibility import (
    ACCEPTED_CHANGED_LINES,
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


def test_fingerprint_ignores_test_files_the_card_does_not_draw() -> None:
    source = _file("src/app.py", additions=4, patch="+a\n+b\n+c\n+d")
    before = assess_eligibility([source, _file("tests/test_app.py", additions=1, patch="+x")])
    after = assess_eligibility([source, _file("tests/test_app.py", additions=2, patch="+x\n+y")])
    new_source = assess_eligibility([source, _file("src/other.py", patch="+z")])

    assert isinstance(before, EligibleDiff) and isinstance(after, EligibleDiff)
    assert isinstance(new_source, EligibleDiff)
    assert before.fingerprint == after.fingerprint
    assert before.fingerprint != new_source.fingerprint


def test_fingerprint_covers_tests_once_the_card_draws_them() -> None:
    source = _file("src/app.py", additions=1, patch="+a")
    before = assess_eligibility([source, _file("tests/test_app.py", additions=5, patch="+x")])
    after = assess_eligibility([source, _file("tests/test_app.py", additions=5, patch="+y")])

    assert isinstance(before, EligibleDiff) and isinstance(after, EligibleDiff)
    assert before.fingerprint != after.fingerprint


def test_line_cap_is_inclusive_and_carries_leeway_past_the_advertised_limit() -> None:
    at_cap = assess_eligibility([_file("a.py", additions=MAX_CHANGED_LINES)])
    over_cap = assess_eligibility([_file("a.py", additions=MAX_CHANGED_LINES + 1)])
    at_leeway = assess_eligibility([_file("a.py", additions=ACCEPTED_CHANGED_LINES)])
    past_leeway = assess_eligibility([_file("a.py", additions=ACCEPTED_CHANGED_LINES + 1)])

    assert isinstance(at_cap, EligibleDiff)
    assert isinstance(over_cap, EligibleDiff)
    assert isinstance(at_leeway, EligibleDiff)
    assert isinstance(past_leeway, Ineligible)
    assert f"limit is {MAX_CHANGED_LINES}" in past_leeway.reason


def test_files_without_a_text_patch_are_refused() -> None:
    verdict = assess_eligibility([_file("logo.png", patch=None)])

    assert isinstance(verdict, Ineligible)
    assert "logo.png" in verdict.reason


def test_no_path_is_refused_for_being_sensitive() -> None:
    """Size and visibility are the only gates; nothing is denied by path."""
    for path in (
        ".github/workflows/ci.yml",
        "agent/auth/session.py",
        "config/secrets.yaml",
        "agent/database/migrations/versions/0012_x.py",
        "ui/package.json",
        "requirements-dev.txt",
        "deploy/.env.production",
        "certs/server.pem",
    ):
        verdict = assess_eligibility([_file(path)])
        assert isinstance(verdict, EligibleDiff), path


def test_a_rename_carrying_a_text_diff_is_eligible() -> None:
    moved = ChangedFile(
        filename="agent/session.py",
        previous_filename="agent/auth/session.py",
        additions=1,
        patch="+x",
    )

    assert isinstance(assess_eligibility([moved]), EligibleDiff)


def test_test_files_are_outside_the_line_cap_and_the_patch_gate() -> None:
    verdict = assess_eligibility(
        [
            _file("agent/app.py", additions=2),
            _file("tests/test_app.py", additions=400, patch=None),
        ]
    )

    assert isinstance(verdict, EligibleDiff)
    assert verdict.changed_lines == 2
    assert verdict.test_lines == 400


def test_test_paths_are_recognised_across_languages() -> None:
    for path in (
        "tests/expedited_review/test_eligibility.py",
        "agent/conftest.py",
        "agent/slack/client_test.go",
        "ui/src/lib/api.test.ts",
        "tests/e2e/tests/expedited_review.spec.ts",
        "internal/testdata/golden.json",
    ):
        assert ChangedFile(filename=path).is_test, path
    for path in ("agent/latest.py", "ui/src/features/contest/Entry.tsx", "docs/protest.md"):
        assert not ChangedFile(filename=path).is_test, path


def test_a_mostly_source_change_keeps_its_tests_off_the_card() -> None:
    files = [_file("agent/app.py", additions=8), _file("tests/test_app.py", additions=3)]

    shown, named = ChangedFile.rendered(files)

    assert [file.filename for file in shown] == ["agent/app.py"]
    assert [file.filename for file in named] == ["tests/test_app.py"]


def test_a_test_heavy_change_shows_its_tests() -> None:
    files = [_file("agent/app.py", additions=2), _file("tests/test_app.py", additions=30)]

    shown, named = ChangedFile.rendered(files)

    assert [file.filename for file in shown] == ["agent/app.py", "tests/test_app.py"]
    assert named == []


def test_a_test_heavy_change_still_only_names_tests_it_cannot_draw() -> None:
    files = [_file("tests/test_app.py", additions=30, patch=None)]

    shown, named = ChangedFile.rendered(files)

    assert shown == []
    assert [file.filename for file in named] == ["tests/test_app.py"]


def test_a_move_into_the_tests_tree_is_not_exempt() -> None:
    """The production file disappears; the voters have to see that."""
    moved = ChangedFile(
        filename="tests/critical.py",
        previous_filename="agent/critical.py",
        status="renamed",
        patch=None,
    )

    assert not moved.is_test
    assert isinstance(assess_eligibility([moved]), Ineligible)


def test_a_move_within_the_tests_tree_stays_exempt() -> None:
    moved = ChangedFile(
        filename="tests/unit/test_app.py",
        previous_filename="tests/test_app.py",
        status="renamed",
        patch=None,
    )

    assert moved.is_test

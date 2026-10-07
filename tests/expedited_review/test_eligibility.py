from openswe.expedited_review.eligibility import (
    ACCEPTED_CHANGED_LINES,
    MAX_CHANGED_LINES,
    ChangedFile,
    EligibleDiff,
    Ineligible,
    assess_eligibility,
    fingerprint_matches,
)


def _file(
    name: str, *, additions: int = 1, deletions: int = 0, patch: str | None = "@@"
) -> ChangedFile:
    return ChangedFile(filename=name, additions=additions, deletions=deletions, patch=patch)


def test_fingerprint_changes_with_the_patch() -> None:
    before = assess_eligibility([_file("src/app.py", patch="+a")])
    after = assess_eligibility([_file("src/app.py", patch="+b")])

    assert isinstance(before, EligibleDiff) and isinstance(after, EligibleDiff)
    assert before.fingerprint != after.fingerprint


def test_tests_growing_past_the_source_keep_a_card_that_did_not_draw_them() -> None:
    source = _file("src/app.py", additions=2, patch="+a\n+b")
    card = assess_eligibility([source, _file("tests/test_app.py", additions=1, patch="+x")])
    grown = [source, _file("tests/test_app.py", additions=9, patch="+x\n+y")]

    assert isinstance(card, EligibleDiff)
    assert fingerprint_matches(grown, card.fingerprint)
    assert not fingerprint_matches([_file("src/app.py", patch="+c"), *grown[1:]], card.fingerprint)


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

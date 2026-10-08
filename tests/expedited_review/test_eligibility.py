from openswe.expedited_review.eligibility import (
    ACCEPTED_CHANGED_LINES,
    MAX_CHANGED_LINES,
    ChangedFile,
    EligibleDiff,
    Exclusion,
    ExpeditedDiff,
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


def test_excluded_hunks_leave_the_card_until_their_content_changes() -> None:
    small = "@@ -1,1 +1,2 @@\n a\n+b"
    big = "@@ -10,1 +11,30 @@\n x\n" + "\n".join(f"+gen{i}" for i in range(29))
    file = ChangedFile(filename="src/app.py", additions=30, deletions=0, patch=f"{small}\n{big}")
    exclusions = Exclusion(
        path="src/app.py", hunks=[11], guideline="Generated", reason="r"
    ).resolve([file])

    assert isinstance(assess_eligibility([file]), Ineligible)
    verdict = assess_eligibility([file], exclusions)
    assert isinstance(verdict, EligibleDiff)
    assert (verdict.changed_lines, verdict.excluded_lines) == (1, 29)
    assert ExpeditedDiff([file], exclusions).shown[0].patch == small

    edited = file.model_copy(update={"patch": f"{small}\n{big}\n+sneaky", "additions": 31})
    assert isinstance(assess_eligibility([edited], exclusions), Ineligible)

    unparsed = file.model_copy(update={"additions": 40})
    verdict = assess_eligibility([unparsed], exclusions)
    assert isinstance(verdict, Ineligible)
    assert "30 of the pull request's 40" in verdict.reason


def test_one_exclusion_hides_only_the_hunk_it_named_among_identical_bodies() -> None:
    body = " x\n+import os"
    file = ChangedFile(
        filename="src/app.py",
        additions=2,
        patch=f"@@ -1,1 +1,2 @@\n{body}\n@@ -40,1 +41,2 @@\n{body}",
    )
    exclusions = Exclusion(path="src/app.py", hunks=[41], guideline="g", reason="r").resolve([file])

    diff = ExpeditedDiff([file], exclusions)
    assert diff.excluded_lines == 1
    assert diff.shown[0].patch == f"@@ -1,1 +1,2 @@\n{body}"

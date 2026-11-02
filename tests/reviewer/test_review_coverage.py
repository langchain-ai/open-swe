from openswe.expedited_review.eligibility import ChangedFile
from openswe.human_review.picking import Area
from openswe.review.coverage import HunkRange, PullRequestLines, ReviewedFile, _FileReview
from openswe.walkthrough.lines import LineSet

_FILE = ChangedFile(
    filename="api.py",
    patch="@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n@@ -20,2 +20,3 @@\n x\n+y\n z",
)
_HARD = Area(("@lc/api",), frozenset({"ada"}), ("api.py",), hard=True)


def _lines(files: list[ReviewedFile]) -> LineSet:
    return PullRequestLines(
        owner="lc",
        repo="r",
        number=1,
        head_sha="h",
        base_sha="b",
        base_ref="main",
        author="",
        files=[_FILE],
        truncated=False,
    ).selected(files)


def test_a_hunk_selects_only_its_changed_lines() -> None:
    assert _lines([ReviewedFile(path="api.py")]).spans() == _FILE.lines.spans()
    second = HunkRange(addition_start=20, addition_count=3, deletion_start=20, deletion_count=2)
    assert _lines([ReviewedFile(path="api.py", hunks=[second])]) == LineSet.of(
        "api.py", "+", [(21, 21)]
    )


def test_only_a_hard_areas_owners_review_satisfies_it() -> None:
    everything = _FILE.lines
    by_grace = _FileReview(_FILE, _HARD, [], [("grace", everything)]).coverage()
    assert (by_grace.state, by_grace.reviewed_lines, by_grace.owner_reviewed_lines) == (
        "reviewed",
        3,
        0,
    )
    assert _FileReview(_FILE, _HARD, [], [("ada", everything)]).coverage().state == (
        "owner_reviewed"
    )
    partly = _FileReview(_FILE, _HARD, ["grace"], [("ada", LineSet.of("api.py", "+", [(2, 2)]))])
    assert partly.coverage().state == "partial"


def test_an_owners_approval_covers_their_files_and_anyones_covers_unowned_ones() -> None:
    approved = _FileReview(_FILE, _HARD, ["Ada"], []).coverage()
    assert (approved.state, approved.approved_by, approved.owner_reviewed_lines) == (
        "owner_reviewed",
        ["Ada"],
        3,
    )
    assert _FileReview(_FILE, None, ["grace"], []).coverage().state == "owner_reviewed"

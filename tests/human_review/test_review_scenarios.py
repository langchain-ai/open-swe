"""How Open SWE assigns reviewers, told as scenarios that run against the real code."""

from tests.human_review.office import (
    LONDON,
    NEW_YORK,
    ReviewOffice,
    dm_to,
    edited,
    joined,
    overdue,
    picked,
    released,
)


async def test_two_unaccepted_picks_escalate_to_the_author(office: ReviewOffice) -> None:
    """Two unaccepted picks escalate to the author.

    Nobody signs up for Ada's pull request. Open SWE picks a code owner, who lets the pick
    lapse, so Open SWE releases them and picks the other owner. That owner is in London, so the
    pick waits for her work day, and its window counts only her work hours. When the second
    pick also lapses, Open SWE stops rotating: she stays assigned, and Ada hears once, when her
    own work day starts.
    """
    ada = office.person("ada", NEW_YORK)
    bob = office.person("bob", NEW_YORK)
    carol = office.person("carol", LONDON)
    office.owns("/api/", bob, carol)
    office.pull_request(author=ada, files=["api/server.py", "api/routes.py"])

    async with office.from_(ada.at("Mon 10:00")):
        with office.step("Ada asks for a review, and nobody signs up within two hours"):
            await office.request_review()
            await office.wait(hours=2)
            office.expect(picked(bob), dm_to(bob, "reviewer_pick"))

        with office.step("Bob ignores the pick for two of his work hours; Carol's day is over"):
            await office.wait(hours=2)
            office.expect(edited(bob), released(bob, cause="expired"))

        with office.step("Carol's work day starts in London, and Open SWE picks her"):
            await office.wait(hours=14)
            office.expect(picked(carol), dm_to(carol, "reviewer_pick"))

        with office.step("Carol lets her pick lapse too, two of her work hours later"):
            await office.wait(hours=2)
            office.expect(
                overdue(carol, cause="rotation_exhausted"), dm_to(carol, "review_reminder")
            )

        with office.step("It is 06:00 in New York, so Ada hears when her day starts at 09:00"):
            await office.wait(hours=3)
            office.expect(dm_to(ada, "review_overdue"))

        with office.step("Nothing more happens, however long the review waits"):
            await office.wait(days=3)
            office.expect()


async def test_a_volunteer_and_github_approvals_cancel_only_the_picks_they_cover(
    office: ReviewOffice,
) -> None:
    """A volunteer and GitHub approvals cancel only the picks they cover.

    Ada's pull request touches two code owner areas, the API and the UI. Open SWE picks Bob for
    the API. Carol, who also owns the API, signs up herself, so Bob's pick is cancelled and his
    DM says why. Carol's approval covers the API, so Open SWE picks Dana for the UI, which
    nobody covers yet. Erin, another UI owner, approves straight on GitHub without being asked:
    Dana's pick is no longer needed, and her DM is edited rather than deleted.
    """
    ada = office.person("ada", NEW_YORK)
    bob = office.person("bob", NEW_YORK)
    carol = office.person("carol", NEW_YORK)
    dana = office.person("dana", NEW_YORK)
    erin = office.person("erin", NEW_YORK)
    office.owns("/api/", bob, carol)
    office.owns("/ui/", dana, erin)
    office.pull_request(author=ada, files=["api/server.py", "api/routes.py", "ui/page.tsx"])

    async with office.from_(ada.at("Mon 10:00")):
        with office.step("Ada asks for a review, and nobody signs up within two hours"):
            await office.request_review()
            await office.wait(hours=2)
            office.expect(picked(bob), dm_to(bob, "reviewer_pick"))

        with office.step("Carol owns the same API code and clicks I'll review on the card"):
            await office.clicks(carol, "ill_review")
            office.expect(
                edited(bob),
                released(bob, cause="claimed_by_overlapping_owner"),
                joined(carol, cause="signed_up"),
            )

        with office.step("Carol approves on GitHub; the UI still has no reviewer"):
            await office.reviews_on_github(carol)
            office.expect(picked(dana), dm_to(dana, "reviewer_pick"))

        with office.step("Half an hour later, Erin approves the UI on GitHub unasked"):
            await office.wait(minutes=30)
            await office.reviews_on_github(erin)
            office.expect(edited(dana), released(dana, cause="approved"))

        with office.step("Every area is approved, so nothing else happens"):
            await office.wait(days=1)
            office.expect()

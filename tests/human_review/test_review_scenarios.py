"""How Open SWE assigns reviewers, told as scenarios that run against the real code."""

from tests.human_review.scenario import (
    LONDON,
    NEW_YORK,
    FollowsInstructions,
    ReviewScenario,
    TakesSuggestions,
    dm_to,
    edited,
    joined,
    overdue,
    picked,
    released,
    requested_on_github,
)


async def test_a_pick_made_at_night_waits_for_the_reviewers_work_day(
    scenario: ReviewScenario,
) -> None:
    """A pick made at night waits for the reviewer's work day.

    Ada asks for a review late in the evening. Nobody signs up, and Open SWE has nobody to
    suggest: the only code owner has no Open SWE account. The review thread's agent picks Eric
    itself at 01:37, from its own reading of the code's history. Eric is on the card at once,
    but nothing reaches him until his work day starts: no GitHub review request and no DM at
    night, and his window to accept starts at 09:00.
    """
    ada = scenario.person("ada", NEW_YORK)
    eric = scenario.person("eric", NEW_YORK)
    scenario.owns("/web/", "vera")
    scenario.pull_request(author=ada, files=["web/page.tsx"])
    scenario.agent = TakesSuggestions(otherwise=eric)

    async with scenario.from_(ada.at("Mon 23:37")):
        with scenario.step("Ada asks for a review late in the evening"):
            await scenario.request_review()
            scenario.expect()

        with scenario.step("Two hours on, at 01:37, the agent picks Eric; nothing reaches him"):
            await scenario.wait(hours=2)
            scenario.expect(picked(eric))

        with scenario.step("Eric's work day starts at 09:00, and he hears about the pick"):
            await scenario.wait(hours=8)
            scenario.expect(requested_on_github(eric), dm_to(eric, "reviewer_pick"))


async def test_two_unaccepted_picks_escalate_to_the_author(scenario: ReviewScenario) -> None:
    """Two unaccepted picks escalate to the author.

    Nobody signs up for Ada's pull request. Open SWE picks a code owner, who lets the pick
    lapse, so Open SWE releases them and picks the other owner. That owner is in London, so the
    pick waits for her work day, and its window counts only her work hours. When the second
    pick also lapses, Open SWE stops rotating: she stays assigned, and Ada hears once, when her
    own work day starts.
    """
    ada = scenario.person("ada", NEW_YORK)
    bob = scenario.person("bob", NEW_YORK)
    carol = scenario.person("carol", LONDON)
    scenario.owns("/api/", bob, carol)
    scenario.pull_request(author=ada, files=["api/server.py", "api/routes.py"])

    async with scenario.from_(ada.at("Mon 10:00")):
        with scenario.step("Ada asks for a review, and nobody signs up within two hours"):
            await scenario.request_review()
            await scenario.wait(hours=2)
            scenario.expect(picked(bob), requested_on_github(bob), dm_to(bob, "reviewer_pick"))

        with scenario.step("Bob ignores the pick for two of his work hours; Carol's day is over"):
            await scenario.wait(hours=2)
            scenario.expect(edited(bob), released(bob, cause="expired"))

        with scenario.step("Carol's work day starts in London, and Open SWE picks her"):
            await scenario.wait(hours=14)
            scenario.expect(
                picked(carol), requested_on_github(carol), dm_to(carol, "reviewer_pick")
            )

        with scenario.step("Carol lets her pick lapse too, two of her work hours later"):
            await scenario.wait(hours=2)
            scenario.expect(overdue(carol, cause="rotation_exhausted"))

        with scenario.step("It is 06:00 in New York, so Ada hears when her day starts at 09:00"):
            await scenario.wait(hours=3)
            scenario.expect(dm_to(ada, "review_overdue"))

        with scenario.step("Nothing more happens, however long the review waits"):
            await scenario.wait(days=3)
            scenario.expect()


async def test_a_volunteer_and_github_approvals_cancel_only_the_picks_they_cover(
    scenario: ReviewScenario,
) -> None:
    """A volunteer and GitHub approvals cancel only the picks they cover.

    Ada's pull request touches two code owner areas, the API and the UI, and the repository's
    reviewer instructions ask for a UI owner's approval on UI changes. Open SWE picks Bob for
    the API. Carol, who also owns the API, signs up herself, so Bob's pick is cancelled and his
    DM says why. Carol's approval covers the API, so the agent picks Dana for the UI, as the
    instructions ask. Erin, another UI owner, approves straight on GitHub without being asked:
    Dana's pick is no longer needed, and her DM is edited rather than deleted.
    """
    ada = scenario.person("ada", NEW_YORK)
    bob = scenario.person("bob", NEW_YORK)
    carol = scenario.person("carol", NEW_YORK)
    dana = scenario.person("dana", NEW_YORK)
    erin = scenario.person("erin", NEW_YORK)
    scenario.owns("/api/", bob, carol)
    scenario.owns("/ui/", dana, erin)
    scenario.reviewer_instructions("UI changes also need an approval from a UI code owner.")
    scenario.pull_request(author=ada, files=["api/server.py", "api/routes.py", "ui/page.tsx"])

    async with scenario.from_(ada.at("Mon 10:00")):
        with scenario.step("Ada asks for a review, and nobody signs up within two hours"):
            await scenario.request_review()
            await scenario.wait(hours=2)
            scenario.expect(picked(bob), requested_on_github(bob), dm_to(bob, "reviewer_pick"))

        with scenario.step("Carol owns the same API code and clicks I'll review on the card"):
            await scenario.clicks(carol, "ill_review")
            scenario.expect(
                edited(bob),
                released(bob, cause="claimed_by_overlapping_owner"),
                joined(carol, cause="signed_up"),
                requested_on_github(carol),
            )

        with scenario.step("Carol approves on GitHub; the instructions want a UI reviewer too"):
            await scenario.reviews_on_github(carol)
            scenario.expect(picked(dana), requested_on_github(dana), dm_to(dana, "reviewer_pick"))

        with scenario.step("Half an hour later, Erin approves the UI on GitHub unasked"):
            await scenario.wait(minutes=30)
            await scenario.reviews_on_github(erin)
            scenario.expect(edited(dana), released(dana, cause="code_owners_approved"))

        with scenario.step("Every area is approved, so nothing else happens"):
            await scenario.wait(days=1)
            scenario.expect()


async def test_the_repositorys_reviewer_instructions_decide_who_is_picked(
    scenario: ReviewScenario,
) -> None:
    """The repository's reviewer instructions decide who is picked.

    Bob and Carol both own the API, and Open SWE would suggest Bob. The repository's
    `.open-swe/REVIEWERS.md` asks for Carol on API changes. The review thread's agent gets
    Open SWE's suggestion and those instructions, and picks Carol.
    """
    ada = scenario.person("ada", NEW_YORK)
    bob = scenario.person("bob", NEW_YORK)
    carol = scenario.person("carol", NEW_YORK)
    scenario.owns("/api/", bob, carol)
    scenario.reviewer_instructions("API changes need a review from @carol, who owns rate limits.")
    scenario.pull_request(author=ada, files=["api/server.py"])
    scenario.agent = FollowsInstructions()

    async with scenario.from_(ada.at("Mon 10:00")):
        with scenario.step("Ada asks for a review, and nobody signs up within two hours"):
            await scenario.request_review()
            await scenario.wait(hours=2)
            scenario.expect(
                picked(carol), requested_on_github(carol), dm_to(carol, "reviewer_pick")
            )


async def test_one_approval_is_enough_without_reviewer_instructions(
    scenario: ReviewScenario,
) -> None:
    """One approval is enough without reviewer instructions.

    Ada's pull request touches the API and the UI. Code owners only guide who reviews, so
    once Bob approves the API, Open SWE does not look for a UI reviewer as well.
    """
    ada = scenario.person("ada", NEW_YORK)
    bob = scenario.person("bob", NEW_YORK)
    dana = scenario.person("dana", NEW_YORK)
    scenario.owns("/api/", bob)
    scenario.owns("/ui/", dana)
    scenario.pull_request(author=ada, files=["api/server.py", "api/routes.py", "ui/page.tsx"])

    async with scenario.from_(ada.at("Mon 10:00")):
        with scenario.step("Ada asks for a review, and nobody signs up within two hours"):
            await scenario.request_review()
            await scenario.wait(hours=2)
            scenario.expect(picked(bob), requested_on_github(bob), dm_to(bob, "reviewer_pick"))

        with scenario.step("Bob accepts and approves; nobody is asked to review the UI"):
            await scenario.clicks(bob, "accept")
            await scenario.reviews_on_github(bob)
            await scenario.wait(days=1)
            scenario.expect(joined(bob, cause="accepted_pick"), edited(bob))


async def test_github_requiring_code_owner_review_asks_every_areas_owners(
    scenario: ReviewScenario,
) -> None:
    """GitHub requiring code owner review asks every area's owners.

    The base branch's protection requires a code owner's approval for every owned file. Bob's
    approval covers the API but not the UI, so one approval is not enough: Open SWE asks the
    implementer for a UI owner, and it picks Dana.
    """
    ada = scenario.person("ada", NEW_YORK)
    bob = scenario.person("bob", NEW_YORK)
    dana = scenario.person("dana", NEW_YORK)
    scenario.owns("/api/", bob)
    scenario.owns("/ui/", dana)
    scenario.requires_code_owner_review()
    scenario.pull_request(author=ada, files=["api/server.py", "api/routes.py", "ui/page.tsx"])

    async with scenario.from_(ada.at("Mon 10:00")):
        with scenario.step("Ada asks for a review, and nobody signs up within two hours"):
            await scenario.request_review()
            await scenario.wait(hours=2)
            scenario.expect(picked(bob), requested_on_github(bob), dm_to(bob, "reviewer_pick"))

        with scenario.step("Bob accepts and approves; the UI still needs one of its owners"):
            await scenario.clicks(bob, "accept")
            await scenario.reviews_on_github(bob)
            scenario.expect(
                joined(bob, cause="accepted_pick"),
                edited(bob),
                picked(dana),
                requested_on_github(dana),
                dm_to(dana, "reviewer_pick"),
            )

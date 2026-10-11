from collections.abc import Iterator

import pytest

from tests.human_review.scenario import ReviewScenario


@pytest.fixture
def scenario(request: pytest.FixtureRequest) -> Iterator[ReviewScenario]:
    """One review scenario; its page is published whether the test passes or not."""
    scenario = ReviewScenario()
    yield scenario
    if scenario.timeline:
        scenario.publish(request.node.name, request.function.__doc__)

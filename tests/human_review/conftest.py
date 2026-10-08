from collections.abc import Iterator

import pytest

from tests.human_review.office import ReviewOffice


@pytest.fixture
def office(request: pytest.FixtureRequest) -> Iterator[ReviewOffice]:
    """A review office for one scenario; its page is published whether it passes or not."""
    office = ReviewOffice()
    yield office
    if office.timeline:
        office.publish(request.node.name, request.function.__doc__)

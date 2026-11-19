from pricing import total


def test_no_discount() -> None:
    assert total([10.0, 5.0]) == 15.0


def test_discount_is_taken_once() -> None:
    assert total([100.0], discount=0.1) == 90.0

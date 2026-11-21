import pytest


@pytest.mark.parametrize(
    ("kept", "route"),
    [
        ("fast", "fast"),
        ("fast_alt", "fast"),
        ("balanced", "balanced"),
        ("performance", "performance"),
        ("default", "default"),
        ("cheapest", None),
        ("", None),
        (None, None),
    ],
)
def test_a_kept_route_reads_as_both_sides_route_it(kept: object, route: str | None) -> None:
    from runtime_api.catalog import kept_route

    assert kept_route(kept) == route

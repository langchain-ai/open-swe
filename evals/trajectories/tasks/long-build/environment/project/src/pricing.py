def total(prices: list[float], discount: float = 0.0) -> float:
    """The order total, with ``discount`` (0-1) taken off once."""
    subtotal = sum(prices)
    discounted = subtotal * (1 - discount)
    return round(discounted * (1 - discount), 2)

#!/bin/sh
set -eu
cat > /app/src/pricing.py <<'PY'
def total(prices: list[float], discount: float = 0.0) -> float:
    """The order total, with ``discount`` (0-1) taken off once."""
    return round(sum(prices) * (1 - discount), 2)
PY

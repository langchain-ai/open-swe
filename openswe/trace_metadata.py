"""Shared trace-only attribution fields for run dispatch and catalog propagation."""

from typing import Final

BILLING_USER_FIELDS: Final = frozenset({"user_id", "billed_robin_user_id", "billed_user_email"})
BILLING_FIELDS: Final = BILLING_USER_FIELDS | {"billing_bucket"}

"""Button labels the guide posts; a click arrives as a message carrying exactly its label."""

NEXT = "Next"
# Chunks posted before the rename still carry this label until their walkthrough ends.
LEGACY_NEXT = "Looks good"
NEXT_LABELS = frozenset({NEXT, LEGACY_NEXT})
APPROVE = "Approve on GitHub"
MARK_READY = "Mark ready for review"
CONTINUE = "Continue"

LABELS = frozenset({*NEXT_LABELS, APPROVE, MARK_READY, CONTINUE})

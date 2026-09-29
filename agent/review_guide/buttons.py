"""Button labels the guide posts; a click arrives as a message carrying exactly its label."""

LOOKS_GOOD = "Looks good"
APPROVE = "Approve on GitHub"
MARK_READY = "Mark ready for review"
CONTINUE = "Continue"

LABELS = frozenset({LOOKS_GOOD, APPROVE, MARK_READY, CONTINUE})
# Only the server attaches these; the guide never offers them in its own replies.
SERVER_ONLY = frozenset({LOOKS_GOOD, CONTINUE})

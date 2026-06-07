"""Greeting handler with state management."""

from src.input_processor import sanitize_input
from src.response_formatter import format_greeting, format_unrecognized


class GreetingState:
    """Tracks greeting history for a session."""

    def __init__(self):
        self.greeting_count = 0
        self.has_greeted = False

    def record_greeting(self):
        self.greeting_count += 1
        self.has_greeted = True


_state = GreetingState()


def process_greeting(input_text):
    """Process a greeting request and return a response message."""
    cleaned = sanitize_input(input_text)
    if cleaned is None:
        return None
    if cleaned == "hello":
        is_returning = _state.has_greeted
        _state.record_greeting()
        return format_greeting(is_returning=is_returning, count=_state.greeting_count)
    return format_unrecognized(input_text.strip() if input_text else "")


def get_state():
    """Return the current greeting state."""
    return _state


def reset_state():
    """Reset greeting state (for testing)."""
    _state.greeting_count = 0
    _state.has_greeted = False

"""Response formatting for greetings."""


def format_greeting(is_returning=False, count=1):
    """Return a greeting message appropriate to the visit context."""
    if count >= 3:
        return "Hello! Welcome back"
    if is_returning:
        return "Hello again!"
    return "Hello! How can I help you today?"


def format_unrecognized(message):
    """Return a response for non-greeting input."""
    return f"You said: {message}. I only respond to hello"

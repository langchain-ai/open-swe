from src.response_formatter import format_greeting, format_unrecognized


class TestFormatGreeting:
    def test_first_greeting(self):
        r = format_greeting(is_returning=False, count=1)
        assert r == "Hello! How can I help you today?"

    def test_returning_greeting(self):
        r = format_greeting(is_returning=True, count=2)
        assert r == "Hello again!"

    def test_welcome_back(self):
        r = format_greeting(is_returning=True, count=3)
        assert r == "Hello! Welcome back"

    def test_welcome_back_high(self):
        r = format_greeting(is_returning=True, count=10)
        assert r == "Hello! Welcome back"

    def test_response_starts_hello(self):
        r = format_greeting(is_returning=False, count=1)
        assert r.startswith("Hello")

    def test_response_ends_properly(self):
        r = format_greeting(is_returning=False, count=1)
        assert r[-1] in (".", "!", "?")


class TestFormatUnrecognized:
    def test_unrecognized_contains_input(self):
        r = format_unrecognized("world")
        assert "world" in r

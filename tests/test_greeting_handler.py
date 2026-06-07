from src.greeting_handler import process_greeting, reset_state


class TestProcessGreeting:
    def setup_method(self):
        reset_state()

    def test_basic_greeting(self):
        r = process_greeting("hello")
        assert r == "Hello! How can I help you today?"
        assert r is not None

    def test_case_insensitive(self):
        reset_state()
        r1 = process_greeting("Hello")
        r2 = process_greeting("HELLO")
        r3 = process_greeting("hElLo")
        assert r1 == "Hello! How can I help you today?"
        assert r2 == "Hello again!"
        assert r3 == "Hello! Welcome back"

    def test_whitespace(self):
        reset_state()
        r1 = process_greeting("  hello  ")
        r2 = process_greeting("hello\n")
        r3 = process_greeting("\thello")
        assert r1 == "Hello! How can I help you today?"
        assert r2 == "Hello again!"
        assert r3 == "Hello! Welcome back"

    def test_empty_input(self):
        reset_state()
        assert process_greeting("") is None
        assert process_greeting("   ") is None
        assert process_greeting(None) is None

    def test_repeated_greetings(self):
        reset_state()
        r1 = process_greeting("hello")
        assert r1 == "Hello! How can I help you today?"
        r2 = process_greeting("hello")
        assert r2 == "Hello again!"
        r3 = process_greeting("hello")
        assert r3 == "Hello! Welcome back"
        r4 = process_greeting("hello")
        assert r4 == "Hello! Welcome back"

    def test_non_hello_input(self):
        reset_state()
        r = process_greeting("world")
        assert "world" in r

    def test_response_format(self):
        reset_state()
        r = process_greeting("hello")
        assert isinstance(r, str)
        assert r[0].isupper()
        assert r[-1] in (".", "!", "?")

import pytest

from src.greeting import process_greeting


class TestGreetingFunction:
    def test_greeting_basic(self):
        result = process_greeting("hello")
        assert result == "Hello! How can I help you today?"

    def test_greeting_uppercase(self):
        result = process_greeting("HELLO")
        assert result == "Hello! How can I help you today?"

    def test_greeting_mixed_case(self):
        result = process_greeting("HeLLo")
        assert result == "Hello! How can I help you today?"

    def test_greeting_with_leading_spaces(self):
        result = process_greeting("  hello")
        assert result == "Hello! How can I help you today?"

    def test_greeting_with_trailing_spaces(self):
        result = process_greeting("hello   ")
        assert result == "Hello! How can I help you today?"

    def test_greeting_with_surrounding_spaces(self):
        result = process_greeting("  hello  ")
        assert result == "Hello! How can I help you today?"

    def test_empty_string(self):
        result = process_greeting("")
        assert result is None or result == ""

    def test_null_input(self):
        with pytest.raises(ValueError, match="Input cannot be None"):
            process_greeting(None)

    def test_whitespace_only(self):
        result = process_greeting("   ")
        assert result is None or result == ""

    def test_hello_with_punctuation(self):
        result = process_greeting("hello!")
        assert result is None or result == ""

    def test_hello_in_sentence(self):
        result = process_greeting("say hello to John")
        assert result is None or result == ""

    def test_multiple_hellos(self):
        for _ in range(3):
            result = process_greeting("hello")
            assert result == "Hello! How can I help you today?"

    def test_unicode_variation(self):
        result = process_greeting("h\u0119ll\u00f6")
        assert result is None or result == ""

    def test_newline_in_input(self):
        result = process_greeting("hello\n")
        assert result == "Hello! How can I help you today?"

    def test_tab_in_input(self):
        result = process_greeting("\thello")
        assert result == "Hello! How can I help you today?"

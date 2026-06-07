"""Tests for input_processor module."""

from src.input_processor import sanitize_input


class TestSanitizeInput:
    def test_normal_input(self):
        assert sanitize_input("hello") == "hello"

    def test_uppercase(self):
        assert sanitize_input("HELLO") == "hello"

    def test_mixed_case(self):
        assert sanitize_input("HeLLo") == "hello"

    def test_leading_whitespace(self):
        assert sanitize_input("  hello") == "hello"

    def test_trailing_whitespace(self):
        assert sanitize_input("hello   ") == "hello"

    def test_surrounding_whitespace(self):
        assert sanitize_input("  hello  ") == "hello"

    def test_newline(self):
        assert sanitize_input("hello\n") == "hello"

    def test_tab(self):
        assert sanitize_input("\thello") == "hello"

    def test_empty_string(self):
        assert sanitize_input("") is None

    def test_whitespace_only(self):
        assert sanitize_input("   ") is None

    def test_none_input(self):
        assert sanitize_input(None) is None

"""Helpers for summarizing diff statistics."""

from __future__ import annotations


def addition_percentage(added_lines: int, total_lines: int) -> float:
    """Return the percentage of changed lines that are additions.

    The result is a value between 0 and 100, e.g. 25.0 means a quarter of the
    changed lines were added.
    """

    return added_lines / total_lines

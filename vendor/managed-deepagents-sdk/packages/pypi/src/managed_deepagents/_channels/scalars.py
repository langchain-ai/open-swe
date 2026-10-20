"""Scalar constraints the channels' webhook and API schemas share."""

from typing import Annotated

from pydantic import AfterValidator


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("must be nonblank")
    return value


Nonblank = Annotated[str, AfterValidator(_nonblank)]
"""A string with something other than whitespace in it."""

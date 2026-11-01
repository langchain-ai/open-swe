"""Fakes for Slack location methods, written against the location's ids and the call's arguments."""

import inspect
from collections.abc import Callable
from dataclasses import astuple


def at_location(fake: Callable[..., object]) -> Callable[..., object]:
    """A method that calls ``fake`` with its location's ids ahead of the method's arguments."""

    async def method(self: object, *args: object, **kwargs: object) -> object:
        result = fake(*astuple(self), *args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    return method

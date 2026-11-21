"""The tool errors that end the run instead of reaching the model."""

from __future__ import annotations


class RunEndingError(RuntimeError):
    """A tool error that ends the run instead of reaching the model.

    MDA returns every other tool error to the model as the tool's result. Raise a
    subclass from a tool or from your own middleware, for example once a sandbox no
    later call can reach has been reported, so the run stops instead of retrying it.
    To end the run on an error you don't own, raise yours `from` it.

    MDA's own errors that end the run are subclasses too, such as
    `CredentialAuthorizationRequiredError`, so `isinstance(error, RunEndingError)`
    tells whether MDA ends the run on an error.
    """

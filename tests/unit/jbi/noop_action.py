"""
A no-op action for testing.
`init` is required.

`init` should return a __call__able
"""

from src.jbi.bugzilla import BugzillaWebhookRequest


def init(**kwargs):
    """Function that takes required and optional params and returns a callable object"""
    return NoopExecutor(**kwargs)


class NoopExecutor:
    """Callable class that encapsulates the no-op action."""

    def __init__(self, **kwargs):
        """Initialize DefaultExecutor Object"""
        self.parameters = kwargs

    def __call__(self, payload: BugzillaWebhookRequest):
        """Called from BZ webhook when no-op action is used. Does nothing."""
        pass

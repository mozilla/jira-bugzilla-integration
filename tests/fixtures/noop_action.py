"""
A no-op action for testing.
`init` is required.

`init` should return a __call__able
"""
from jbi import Operation


def init(**parameters):
    return lambda bug, event: (
        True,
        {"parameters": parameters, "bug": bug.json(), "event": event.json()},
    )

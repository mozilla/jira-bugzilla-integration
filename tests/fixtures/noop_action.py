"""
A no-op action for testing.
`init` is required.

`init` should return a __call__able
"""
from jbi import Operation


def init(parameters):
    return lambda context: (
        True,
        {
            "parameters": parameters.dict(),
            "bug": context.bug.json(),
            "event": context.event.json(),
        },
    )

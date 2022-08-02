"""
A no-op action for testing.
`init` is required.

`init` should return a __call__able
"""
from src.jbi import Operation


def init(**parameters):
    return lambda payload: (
        True,
        {"parameters": parameters, "payload": payload.json()},
    )

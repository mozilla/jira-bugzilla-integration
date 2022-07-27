"""
A no-op action for testing.
`init` is required.

`init` should return a __call__able
"""
from src.jbi import Operations


def init(**parameters):
    return lambda payload: (
        Operations.CREATE,
        {"parameters": parameters, "payload": payload.json()},
    )

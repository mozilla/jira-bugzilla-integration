"""
A no-op action for testing.
`init` is required.

`init` should return a __call__able
"""


def init(**parameters):
    return lambda payload: {"parameters": parameters, "payload": payload.json()}

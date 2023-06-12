"""
Module domain specific code related to JBI.

This part of the code is not aware of the HTTP context it runs in.
"""
from enum import Enum


class Operation(str, Enum):
    """Enumeration of possible operations logged during WebHook execution."""

    HANDLE = "handle"
    EXECUTE = "execute"
    IGNORE = "ignore"
    SUCCESS = "success"

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    COMMENT = "comment"
    LINK = "link"


ActionResult = tuple[bool, dict]


class IncompleteStepError(Exception):
    """Raised when a step could not complete successfully."""

    def __init__(self, context, *args: object) -> None:
        super().__init__(*args)
        self.context = context

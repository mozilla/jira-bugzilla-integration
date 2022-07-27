"""
Module domain specific code related to JBI.

This part of the code is not aware of the HTTP context it runs in.
"""
from typing import Dict, NewType, Tuple

Operation = NewType("Operation", str)

ActionResult = Tuple[Operation, Dict]


class Operations:
    """Enumeration of possible operations logged during WebHook execution."""

    HANDLE = Operation("handle")
    EXECUTE = Operation("execute")
    IGNORE = Operation("ignore")

    CREATE = Operation("create")
    UPDATE = Operation("update")
    DELETE = Operation("delete")
    COMMENT = Operation("comment")
    LINK = Operation("link")

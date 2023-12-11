"""
Dedicated module for logging configuration and setup
"""
import logging
import logging.config
import sys
import time
from datetime import datetime
from typing import Optional

from asgi_correlation_id import CorrelationIdFilter
from fastapi import Request
from pydantic import BaseModel

from jbi.environment import get_settings

settings = get_settings()


class RequestIdFilter(CorrelationIdFilter):
    """Renames `correlation_id` log field to `rid`"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def filter(self, record) -> bool:
        result = super().filter(record)  # Apply the existing filter

        # Rename the field from 'correlation_id' to 'rid'
        if hasattr(record, "correlation_id"):
            setattr(record, "rid", getattr(record, "correlation_id"))
            delattr(record, "correlation_id")

        return result


CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {
            "()": RequestIdFilter,
            "uuid_length": 32,
            "default_value": "-",
        },
    },
    "formatters": {
        "mozlog_json": {
            "()": "dockerflow.logging.JsonLogFormatter",
            "logger_name": "jbi",
        },
        "text": {
            "format": "%(asctime)s %(levelname)-8s [%(rid)s] %(name)-15s %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "level": settings.log_level.upper(),
            "class": "logging.StreamHandler",
            "filters": ["request_id"],
            "formatter": "text"
            if settings.log_format.lower() == "text"
            else "mozlog_json",
            "stream": sys.stdout,
        },
        "null": {
            "class": "logging.NullHandler",
        },
    },
    "loggers": {
        "": {"handlers": ["console"]},
        "request.summary": {"level": logging.INFO},
        "jbi": {"level": logging.DEBUG},
        "uvicorn": {"level": logging.INFO},
        "uvicorn.access": {"handlers": ["null"], "propagate": False},
    },
}


class RequestSummary(BaseModel):
    """Request summary as specified by MozLog format"""

    agent: str
    path: str
    method: str
    lang: Optional[str]
    querystring: str
    errno: int
    t: int
    time: str
    status_code: int


def format_request_summary_fields(
    request: Request, request_time: float, status_code: int
) -> dict:
    """Prepare Fields for Mozlog request summary"""

    current_time = time.time()
    return RequestSummary(
        agent=request.headers.get("User-Agent", ""),
        path=request.url.path,
        method=request.method,
        lang=request.headers.get("Accept-Language"),
        querystring=str(dict(request.query_params)),
        errno=0,
        t=int((current_time - request_time) * 1000.0),
        time=datetime.fromtimestamp(current_time).isoformat(),
        status_code=status_code,
    ).model_dump()

"""
Dedicated module for logging configuration and setup
"""
import logging
import logging.config
import sys

from asgi_correlation_id import CorrelationIdFilter

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

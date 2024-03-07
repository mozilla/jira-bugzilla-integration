"""
Dedicated module for logging configuration and setup
"""

import logging
import logging.config
import sys

from jbi.environment import get_settings

settings = get_settings()


CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {
            "()": "dockerflow.logging.RequestIdLogFilter",
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

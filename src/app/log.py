"""
Dedicated module for logging configuration and setup
"""
import logging
import logging.config
import sys

from src.app.environment import get_settings

settings = get_settings()

CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "mozlog_json": {
            "()": "dockerflow.logging.JsonLogFormatter",
            "logger_name": "jbi",
        },
    },
    "handlers": {
        "console": {
            "level": settings.log_level.upper(),
            "class": "logging.StreamHandler",
            "formatter": "mozlog_json",
            "stream": sys.stdout,
        },
        "null": {
            "class": "logging.NullHandler",
        },
    },
    "loggers": {
        "": {"handlers": ["console"]},
        "request.summary": {"level": logging.INFO},
        "src": {"level": logging.DEBUG},
        "uvicorn": {"level": logging.INFO},
        "uvicorn.access": {"handlers": ["null"], "propagate": False},
    },
}

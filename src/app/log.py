"""
Dedicated module for logging configuration and setup
"""
import logging
import logging.config
import sys

from src.app.environment import get_settings

settings = get_settings()


def configure_logging():
    """Configure logging; mozlog format for Dockerflow"""
    logging_config = {
        "version": 1,
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
            }
        },
        "loggers": {
            "request.summary": {"handlers": ["console"], "level": "INFO"},
            "backoff": {"handlers": ["console"], "level": "INFO"},
            "src.jbi": {"handlers": ["console"], "level": "DEBUG"},
        },
    }

    logging.config.dictConfig(logging_config)

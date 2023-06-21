"""Contains code common to all services

ServiceHealth: Return type that service health checks should use
InstrumentedClient: wraps service clients so that we can track their usage
"""
import logging
from functools import wraps
from typing import Sequence, Type

import backoff
from statsd.defaults.env import statsd

from jbi import environment

settings = environment.get_settings()

logger = logging.getLogger(__name__)


ServiceHealth = dict[str, bool]


def instrument(prefix: str, exceptions: Sequence[Type[Exception]], **backoff_params):
    """This decorator wraps a function such that it increments a counter every
    time it is called and times its execution. It retries the function if the
    specified exceptions are raised.
    """

    def decorator(func):
        @wraps(func)
        @backoff.on_exception(
            backoff.expo,
            exceptions,
            max_tries=settings.max_retries + 1,
            **backoff_params,
        )
        def wrapper(*args, **kwargs):
            # Increment the call counter.
            statsd.incr(f"jbi.{prefix}.methods.{func.__name__}.count")
            # Time its execution.
            with statsd.timer(f"jbi.{prefix}.methods.{func.__name__}.timer"):
                return func(*args, **kwargs)

        return wrapper

    return decorator

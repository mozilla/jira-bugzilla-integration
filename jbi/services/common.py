import logging

import backoff
from statsd.defaults.env import statsd

from jbi import environment

settings = environment.get_settings()

logger = logging.getLogger(__name__)


ServiceHealth = dict[str, bool]


class InstrumentedClient:
    """This class wraps an object and increments a counter every time
    the specified methods are called, and times their execution.
    It retries the methods if the specified exceptions are raised.
    """

    def __init__(self, wrapped, prefix, methods, exceptions):
        self.wrapped = wrapped
        self.prefix = prefix
        self.methods = methods
        self.exceptions = exceptions

    def __getattr__(self, attr):
        if attr not in self.methods:
            return getattr(self.wrapped, attr)

        @backoff.on_exception(
            backoff.expo,
            self.exceptions,
            max_tries=settings.max_retries + 1,
        )
        def wrapped_func(*args, **kwargs):
            # Increment the call counter.
            statsd.incr(f"jbi.{self.prefix}.methods.{attr}.count")
            # Time its execution.
            with statsd.timer(f"jbi.{self.prefix}.methods.{attr}.timer"):
                return getattr(self.wrapped, attr)(*args, **kwargs)

        # The method was not called yet.
        return wrapped_func

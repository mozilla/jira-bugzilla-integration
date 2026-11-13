"""Keep secrets out of logs."""

import re

# `token` is how the Bugzilla webhook authenticates (it can only send a URL);
# the others cover the obvious spellings so a future producer does not leak.
_SECRET_PARAMS = re.compile(
    r"(?P<prefix>(?:^|[&?]))(?P<name>token|api[_-]?key|key|secret|password)=[^&]*",
    re.IGNORECASE,
)


def redact_querystring(querystring: str) -> str:
    """Replace the value of secret-looking query parameters with a placeholder."""
    return _SECRET_PARAMS.sub(r"\g<prefix>\g<name>=[redacted]", querystring)

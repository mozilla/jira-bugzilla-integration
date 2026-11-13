"""Verify the signature Jira puts on a native webhook delivery.

A webhook registered in Jira's admin UI cannot send custom headers, so it cannot
carry an API key. When the webhook has a *secret*, Jira instead signs each
delivery: ``X-Hub-Signature: sha256=<hex>``, where the hex is the HMAC-SHA256 of
the **raw request body** keyed with the secret.

Two details decide whether this is secure:

* The HMAC is over the bytes Jira sent. Parsing the JSON and serialising it
  again changes them, so the check must run on the body as received.
* The comparison is constant-time, so the response time does not leak how much
  of a guessed signature was right.
"""

import hashlib
import hmac
from typing import Iterable, Optional

SIGNATURE_HEADER = "X-Hub-Signature"
_PREFIX = "sha256="


def sign(body: bytes, secret: str) -> str:
    """Return the header value Jira would send for this body and secret."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"{_PREFIX}{digest}"


def is_valid_signature(
    body: bytes, header: Optional[str], secrets: Iterable[Optional[str]]
) -> bool:
    """Whether `header` is a valid signature of `body` under any of `secrets`.

    Several secrets are accepted so the secret can be rotated without a window
    in which Jira's deliveries are rejected: configure the new one next to the
    old one, update Jira, then drop the old one.

    Only ``sha256`` is accepted. A header naming another algorithm is rejected
    rather than verified with it, so a sender cannot pick a weaker one.
    """
    if not header or not header.startswith(_PREFIX):
        return False
    provided = header[len(_PREFIX) :].strip().lower()
    candidates = [secret for secret in secrets if secret]
    # Check every candidate (no early exit on the first match) so the time taken
    # does not reveal which of the configured secrets was used.
    results = [
        hmac.compare_digest(sign(body, secret)[len(_PREFIX) :], provided)
        for secret in candidates
    ]
    return any(results)

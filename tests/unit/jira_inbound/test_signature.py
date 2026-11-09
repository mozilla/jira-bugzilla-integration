"""Tests for verifying Jira's `X-Hub-Signature`."""

import hashlib
import hmac

import pytest

from jbi.jira_inbound.signature import is_valid_signature, sign

SECRET = "test-webhook-secret"  # pragma: allowlist secret
OTHER = "some-other-secret"  # pragma: allowlist secret
BODY = b'{"issue": {"key": "JBI-1"}, "comment": {"body": "line1\nline2"}}'


def test_a_correctly_signed_body_is_valid():
    assert is_valid_signature(BODY, sign(BODY, SECRET), [SECRET])


def test_sign_is_hmac_sha256_over_the_raw_body():
    """Pinned against the construction Jira documents, not against `sign`."""
    expected = hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()

    assert sign(BODY, SECRET) == f"sha256={expected}"


def test_a_different_secret_is_rejected():
    assert not is_valid_signature(BODY, sign(BODY, OTHER), [SECRET])


def test_a_tampered_body_is_rejected():
    signature = sign(BODY, SECRET)

    assert not is_valid_signature(BODY + b" ", signature, [SECRET])


def test_re_serialising_the_body_breaks_the_signature():
    """Why the check must use the raw bytes: equivalent JSON is not the same
    bytes, and the signature covers bytes."""
    import json

    signature = sign(BODY, SECRET)
    reserialised = json.dumps(json.loads(BODY, strict=False)).encode()

    assert not is_valid_signature(reserialised, signature, [SECRET])


@pytest.mark.parametrize("header", [None, "", "sha256=", "sha256=nothex", "garbage"])
def test_missing_or_malformed_headers_are_rejected(header):
    assert not is_valid_signature(BODY, header, [SECRET])


def test_other_algorithms_are_not_accepted():
    """A sender must not be able to choose a weaker hash."""
    sha1 = "sha1=" + hmac.new(SECRET.encode(), BODY, hashlib.sha1).hexdigest()

    assert not is_valid_signature(BODY, sha1, [SECRET])


def test_hex_case_does_not_matter():
    assert is_valid_signature(
        BODY, sign(BODY, SECRET).upper().replace("SHA256=", "sha256="), [SECRET]
    )


def test_with_no_secret_configured_nothing_is_valid():
    """An unset secret must never behave like an empty key that anyone can
    sign with."""
    forged = sign(BODY, "")

    assert not is_valid_signature(BODY, forged, [None])
    assert not is_valid_signature(BODY, forged, [""])
    assert not is_valid_signature(BODY, forged, [])


def test_either_secret_is_accepted_during_rotation():
    assert is_valid_signature(BODY, sign(BODY, OTHER), [SECRET, OTHER])
    assert is_valid_signature(BODY, sign(BODY, SECRET), [SECRET, OTHER])
    assert not is_valid_signature(BODY, sign(BODY, "third"), [SECRET, OTHER])

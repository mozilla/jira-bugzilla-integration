import pytest

from jbi.common.redact import redact_querystring


@pytest.mark.parametrize(
    "querystring, expected",
    [
        ("", ""),
        ("token=abc123", "token=[redacted]"),
        ("a=1&token=abc123&b=2", "a=1&token=[redacted]&b=2"),
        ("TOKEN=abc123", "TOKEN=[redacted]"),
        ("api_key=abc&x=1", "api_key=[redacted]&x=1"),
        ("page=2&sort=asc", "page=2&sort=asc"),
        # A secret containing characters that look like separators.
        ("token=Zx9Qk*Lm2Pr@Vt5Ws8Yb1Nc4Dh7Fj0Gu3Ea6Ti.", "token=[redacted]"),
    ],
)
def test_redact_querystring(querystring, expected):
    assert redact_querystring(querystring) == expected


def test_request_summary_does_not_log_the_token(anon_client, test_api_key, capturelogs):
    import logging

    with capturelogs.for_logger("request.summary").at_level(logging.INFO):
        anon_client.get(f"/__lbheartbeat__?token={test_api_key}")

    logged = [str(getattr(r, "querystring", "")) for r in capturelogs.records]
    assert any("token=[redacted]" in value for value in logged)
    assert not any(test_api_key in value for value in logged)

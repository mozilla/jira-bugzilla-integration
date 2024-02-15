#!/usr/bin/env python

import os

import backoff
import requests

PORT = os.getenv("PORT", "8000")


@backoff.on_exception(
    backoff.expo,
    requests.exceptions.RequestException,
    max_tries=5,
)
def check_server():
    url = f"http://0.0.0.0:{PORT}"
    response = requests.get(f"{url}/")
    response.raise_for_status()

    hb_response = requests.get(f"{url}/__heartbeat__")
    hb_details = hb_response.json()
    # Check that pandoc is installed, but ignore other checks
    # like connection to Jira or Bugzilla.
    assert hb_details["checks"]["jira.pandoc_install"] == "ok"
    print("Ok")


if __name__ == "__main__":
    check_server()

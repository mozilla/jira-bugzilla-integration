#!/usr/bin/env python

import os

import requests
from requests.adapters import HTTPAdapter, Retry

PORT = os.environ["PORT"]

session = requests.Session()

retries = Retry(total=5, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
session.mount("http://", HTTPAdapter(max_retries=retries))
if __name__ == "__main__":
    url = f"http://0.0.0.0:{PORT}"
    response = session.get(f"{url}/")
    response.raise_for_status()

    hb_response = session.get(f"{url}/__heartbeat__")
    assert hb_response.json["pandoc_install"]

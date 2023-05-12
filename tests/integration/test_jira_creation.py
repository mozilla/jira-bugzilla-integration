import os

from fastapi.encoders import jsonable_encoder
import requests


JBI_SERVER = os.getenv("SERVER", "http://localhost:8000")


def test_basic_jira_create(webhook_create_example):
    url = f"{JBI_SERVER}/bugzilla_webhook"

    webhook_create_example.bug.id = 1612587

    # print("whiteboard tag", webhook_create_example.bug.whiteboard)
    resp = requests.post(url, json=jsonable_encoder(webhook_create_example))

    resp.raise_for_status()

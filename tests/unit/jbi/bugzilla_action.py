from bugzilla import Bugzilla
from requests import Session

session = Session()


class FakeBugzillaAction:
    def __init__(self, **params):
        self.bz = Bugzilla(url=None, requests_session=session)

    def __call__(self, payload):
        return {"payload": payload}


def init():
    return FakeBugzillaAction()

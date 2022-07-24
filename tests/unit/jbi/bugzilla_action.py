from bugzilla import Bugzilla
from requests import Session

session = Session()


class TestBugzillaAction:
    def __init__(self):
        self.bz = Bugzilla(url=None, requests_session=session)

    def __call__(self):
        return lambda: "test"


def init():
    return TestBugzillaAction()

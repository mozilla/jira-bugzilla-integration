class FakeBugzillaAction:
    def __call__(self, payload):
        return {"payload": payload}


def init():
    return FakeBugzillaAction()

class FakeBugzillaAction:
    def __call__(self, bug, event):
        return {"bug": bug, "event": event}


def init():
    return FakeBugzillaAction()

def test_attachments_flags(bug_factory):
    bug_factory.build(
        with_attachment=True,
        attachment__flags=[
            {"id": 2302739, "name": "approval-mozilla-beta", "value": "?"}
        ],
    )
    # not raising is a success

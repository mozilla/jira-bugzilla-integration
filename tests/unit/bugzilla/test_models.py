def test_attachments_flags(bug_factory):
    bug_factory.build(
        with_attachment=True,
        attachment__flags=[
            {"id": 2302739, "name": "approval-mozilla-beta", "value": "?"}
        ],
    )
    # not raising is a success

def test_patch_attachment_returns_valid_phabricator_url(bug_factory):
    bug = bug_factory.build(
        with_attachment=True,
        attachment__is_patch=True,
        attachment__file_name="phabricator-D262912-url.txt",
        attachment__content_type="text/x-phabricator-request",
    )
    base_url = "https://phabricator.services.mozilla.com"
    assert bug.attachment.phabricator_url(base_url=base_url) == "https://phabricator.services.mozilla.com/D262912"

def test_non_patch_attachment_returns_no_phabricator_url(bug_factory):
    bug = bug_factory.build(
        with_attachment=True,
        attachment__file_name="screenshot.png",
        attachment__content_type="image/png",
    )
    base_url = "https://phabricator.services.mozilla.com"
    assert bug.attachment.phabricator_url(base_url=base_url) is None

def test_patch_attachment_with_unexpected_file_name_returns_no_phabricator_url(bug_factory):
    bug = bug_factory.build(
        with_attachment=True,
        attachment__file_name="phabricator-F262912-url.txt", # normally should be "phabricator-D<>-url.txt"
        attachment__content_type="text/x-phabricator-request",
    )
    base_url = "https://phabricator.services.mozilla.com"
    assert bug.attachment.phabricator_url(base_url=base_url) is None

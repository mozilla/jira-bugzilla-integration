from textwrap import dedent

from jbi.jira.utils import markdown_to_jira


def test_markdown_to_jira():
    markdown = dedent(
        """
    Mixed nested lists

    * a
    * bulleted
      - with
      - nested
        1. nested-nested
      - numbered
    * list

    this was `inline` value ``that`` is turned into ```monospace``` tag.

    this sentence __has__ **bold** and _has_ *italic*.

    this was ~~wrong~~.
    """
    ).lstrip()

    jira = dedent(
        """
    Mixed nested lists

    * a
    * bulleted
    ** with
    ** nested
    **# nested-nested
    ** numbered
    * list

    this was {{inline}} value {{that}} is turned into {{monospace}} tag.

    this sentence *has* *bold* and _has_ _italic_.

    this was -wrong-.
    """
    ).strip()

    assert markdown_to_jira(markdown) == jira

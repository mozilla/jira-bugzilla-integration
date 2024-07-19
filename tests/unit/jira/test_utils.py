from textwrap import dedent

import pytest

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

    List without newline:
    * one
    * two

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

    List without newline:

    * one
    * two

    this was {{inline}} value {{that}} is turned into {{monospace}} tag.

    this sentence *has* *bold* and _has_ _italic_.

    this was -wrong-.
    """
    ).strip()

    assert markdown_to_jira(markdown) == jira


def test_markdown_to_jira_with_malformed_input():
    assert markdown_to_jira("[link|http://noend") == "\\[link|http://noend"


@pytest.mark.parametrize(
    "markdown, expected, max_length",
    [
        ("a" * 10, "aaaaa", 5),
        ("aa aaa", "aa", 5),
        ("aa\naaa", "aa", 5),
        ("aa\taaa", "aa", 5),
        ("aaaaaa", "aaaaa", 5),
        ("aaaaa ", "aaaaa", 5),
        ("`fo` `fo`", "{{fo}}", 9),
    ],
)
def test_markdown_to_jira_with_max_chars(markdown, expected, max_length):
    assert markdown_to_jira(markdown, max_length=max_length) == expected

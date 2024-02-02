import logging

import pypandoc  # type: ignore

logging.getLogger("pypandoc").addHandler(logging.NullHandler())


def markdown_to_jira(markdown: str) -> str:
    """
    Convert markdown content into Jira specific markup language.
    """
    return pypandoc.convert_text(markdown, "jira", format="md").strip()  # type: ignore

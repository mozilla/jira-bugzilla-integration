import logging

import pypandoc  # type: ignore

logging.getLogger("pypandoc").addHandler(logging.NullHandler())


def markdown_to_jira(markdown: str, max_length: int = 0) -> str:
    """
    Convert markdown content into Jira specific markup language.
    """
    if max_length > 0 and len(markdown) > max_length:
        # Truncate on last word.
        markdown = markdown[:max_length].rsplit(maxsplit=1)[0]
    return pypandoc.convert_text(markdown, "jira", format="gfm").strip()  # type: ignore

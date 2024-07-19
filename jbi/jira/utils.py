import logging

import pypandoc  # type: ignore

logging.getLogger("pypandoc").addHandler(logging.NullHandler())


def markdown_to_jira(markdown: str, max_length: int = 0) -> str:
    """
    Convert markdown content into Jira specific markup language.
    """
    jira_output = pypandoc.convert_text(markdown, "jira", format="gfm").strip()
    if max_length > 0 and len(jira_output) > max_length:
        # Truncate on last word.
        jira_output = jira_output[:max_length].rsplit(maxsplit=1)[0]
    return jira_output  # type: ignore

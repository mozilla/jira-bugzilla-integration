import re


def convert(markdown: str) -> str:
    """
    Best effort to transform Bugzilla markdown to Jira text formatting.
    https://jira.atlassian.com/secure/WikiRendererHelpAction.jspa?section=all
    """
    result = markdown

    # Convert multiline code blocks
    result = re.sub(
        r"```(\w+)?\n(.*?)\n```",
        lambda match: f"{{code:{match.group(1)}}}\n{match.group(2)}\n{{code}}"
        if match.group(1)
        else f"{{code}}\n{match.group(2)}\n{{code}}",
        result,
        flags=re.MULTILINE | re.DOTALL,
    )

    converted = []
    block = False
    quote = False
    for line in result.splitlines():
        if line.startswith(">"):
            if not quote:
                converted.append("{quote}")
            quote = True
        else:
            if quote:
                converted.append("{quote}")
            quote = False

        if quote:
            converted.append(convert_line(re.sub("^>\\s*", "", line)))
        else:
            if line.startswith("{code"):
                block = not block
            if block:
                converted.append(line)
            else:
                converted.append(convert_line(line))

    return "\n".join(converted)


def convert_line(line: str) -> str:
    # Titles
    for level in range(7, 0, -1):
        line = re.sub("^" + ("#" * level) + "\\s*(.+)", f"h{level}. \\1", line)
    # Links
    line = re.sub(r"\[(.+?)\]\((.+?)\)", r"[\1|\2]", line)
    # Strikethrough
    line = re.sub(r"~~(.+?)~~", r"-\1-", line)
    # Bold
    line = re.sub(r"\*\*(.+?)\*\*", r"*\1*", line)
    line = re.sub(r"__(.+?)__", r"*\1*", line)
    # Italic
    line = re.sub(r"\*(.+?)\*", r"_\1_", line)
    line = re.sub(r"_(.+?)_", r"_\1_", line)
    return line

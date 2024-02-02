import re


def convert(markdown: str) -> str:
    """
    Best effort to transform Bugzilla markdown to Jira text formatting.
    https://jira.atlassian.com/secure/WikiRendererHelpAction.jspa?section=all

    Known Limitations:
    - No mixed nested lists
    - No nested quoted text (eg. quote of quote)
    - No images
    - No tables
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
        # Turn adjacent lines of quoted text (`> `)
        # into a {quote} block.
        if line.startswith(">"):
            if not quote:  # first quoted line
                converted.append("{quote}")
            quote = True
        else:
            if quote:  # last quoted line
                converted.append("{quote}")
            quote = False

        if quote:
            # Strip leading `> ` from the text and convert syntax.
            converted.append(convert_line(re.sub("^>\\s*", "", line)))
        else:
            # Do not convert text that's within a {code} block.
            if line.startswith("{code"):
                block = not block
            if block:
                converted.append(line)  # raw
            else:
                converted.append(convert_line(line))

    return "\n".join(converted)


def convert_line(line: str) -> str:
    """
    Basic conversion of Markdown syntax to Jira syntax.
    """
    # Titles
    for level in range(7, 0, -1):
        line = re.sub("^" + ("#" * level) + "\\s*(.+)", f"h{level}. \\1", line)
    # Lists
    for level in range(4, -1, -1):
        line = re.sub(
            "^" + (r"\s{2}" * level) + r"(\*|\-|\d+\.)",
            lambda match: r"#" * (level + 1)
            if "." in match.group(1)
            else match.group(1) * (level + 1),
            line,
        )
    # Links
    line = re.sub(r"\[(.+?)\]\((.+?)\)", r"[\1|\2]", line)
    # Strikethrough
    line = re.sub(r"~~(.+?)~~", r"-\1-", line)
    # Italic.
    # _this_ but not __this__
    line = re.sub(r"([^_])_([^_]+?)_", r"\1_\2_", line)
    # *this* but not **this**
    line = re.sub(r"([^\*])\*([^\*]+?)\*", r"\1_\2_", line)
    # Bold
    line = re.sub(r"\*\*(.+?)\*\*", r"*\1*", line)
    line = re.sub(r"__(.+?)__", r"*\1*", line)
    # Monospace
    line = re.sub(r"`+(.+?)`+", r"{{\1}}", line)
    return line

"""Compare only known Linear link serialization changes; unknown differences fail closed."""

import re

MARKDOWN_LINK = (
    r"\[(?P<label>[^\\\[\]\r\n<>*_~&]+)\]"
    r"\((?:<(?P<angle>https://[^\s<>]+)>|(?P<plain>https://[^\s<>()[\]]+))\)"
)
DOCUMENT_LINK = (
    r'<document id="(?P<id>[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})" '
    r'href="(?P<href>https://linear\.app/[^\s<>\"]+)">'
    r"(?P<title>[^\\\[\]<>\r\n*_~&]+)</document>"
)
LINK = re.compile(r"(?<![\\!])(?:" + MARKDOWN_LINK + "|" + DOCUMENT_LINK + ")")
CODE = re.compile(r"`|~{3,}|<(?:pre|code)(?:\s|>)", re.IGNORECASE)
INDENTED = re.compile(r"^(?: {4}| {0,3}\t)")


def link_tokens(text):
    """Retain every non-link byte and do not reinterpret code or nested markup.

    This deliberately is not a general Markdown parser. Documents with backticks,
    tilde fences or HTML code blocks stay exact; indented lines stay exact too.
    Unsupported serialization changes need an explicit recovery, not a false receipt.
    """
    if CODE.search(text):
        return [("text", text)]
    tokens = []
    for line in text.splitlines(keepends=True):
        if INDENTED.match(line):
            tokens.append(("text", line))
            continue
        offset = 0
        for match in LINK.finditer(line):
            tokens.append(("text", line[offset : match.start()]))
            if match["id"]:
                tokens.append(("link", match["title"], match["href"], match["id"]))
            else:
                tokens.append(("link", match["label"], match["angle"] or match["plain"], None))
            offset = match.end()
        tokens.append(("text", line[offset:]))
    # Unconsumed brackets/HTML may be a nested link, image, block quote, raw
    # HTML or escaped syntax. Only ordinary prose links are canonicalized.
    if any(token[0] == "text" and re.search(r"[\[\]<>]", token[1]) for token in tokens):
        return [("text", text)]
    return tokens


def equivalent_content(expected, actual):
    if not isinstance(expected, str) or not isinstance(actual, str):
        return False
    if expected == actual:
        return True
    left, right = link_tokens(expected), link_tokens(actual)
    if len(left) != len(right):
        return False
    for before, after in zip(left, right, strict=True):
        if before[0] != after[0]:
            return False
        if before[0] == "text":
            if before != after:
                return False
        elif before[1:3] != after[1:3] or (
            before[3] is not None and after[3] is not None and before[3] != after[3]
        ):
            return False
    return True


def matches_document(document, fields):
    return (
        isinstance(document, dict)
        and document.get("title") == fields["title"]
        and equivalent_content(fields["content"], document.get("content"))
    )

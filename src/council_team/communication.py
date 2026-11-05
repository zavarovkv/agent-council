"""Shared limits for newly authored coordination, separate from full artifacts."""

BRIEF_LIMIT = 1200
COMMENT_LIMIT = 700
BRIEF_GUIDANCE = (
    "Use 3–5 short bullets for the outcome, scope and checks; link full requirements "
    "in an authorized working document. Preserve essential constraints."
)
COMMENT_GUIDANCE = (
    "Post one meaningful update; put full detail in save_document or an authorized "
    "artifact instead of splitting it across multiple comments."
)


class TextLimitError(ValueError):
    """A safe rejection before this attempt writes coordination text."""


def check_brief(value):
    if isinstance(value, str) and len(value) > BRIEF_LIMIT:
        raise TextLimitError(
            f"Task description exceeds {BRIEF_LIMIT} characters. {BRIEF_GUIDANCE} "
            "This attempt made no new write. If the key was already accepted, "
            "inspect/reconcile it unchanged; otherwise shorten before retrying."
        )


def check_comment(value):
    if isinstance(value, str) and len(value) > COMMENT_LIMIT:
        raise TextLimitError(
            f"Comment exceeds {COMMENT_LIMIT} characters. {COMMENT_GUIDANCE} "
            "This attempt made no new write. If the key was already accepted, "
            "inspect/reconcile it unchanged; otherwise shorten before retrying."
        )

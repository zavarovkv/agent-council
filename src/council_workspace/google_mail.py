"""Bounded Gmail reads and explicit, journaled drafts and sends."""

import base64
import hashlib
from email.message import EmailMessage

from .google_calendar import email


def message_view(item):
    payload = item.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    parts, bodies, attachments = [payload], [], []
    visited = 0
    remaining = 32000
    while parts and visited < 100:
        part = parts.pop(0)
        visited += 1
        parts.extend(part.get("parts", []))
        if part.get("filename"):
            attachments.append(
                {
                    "name": part["filename"],
                    "mime_type": part.get("mimeType"),
                    "size": part.get("body", {}).get("size"),
                    "id": part.get("body", {}).get("attachmentId"),
                }
            )
        elif part.get("mimeType") in {"text/plain", "text/html"}:
            encoded = part.get("body", {}).get("data", "")
            if len(encoded) > 500000:
                bodies.append(
                    {
                        "mime_type": part["mimeType"],
                        "text": "[Body exceeds the reading limit]",
                        "truncated": True,
                    }
                )
            else:
                content = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode(
                    "utf-8", "replace"
                )
                bodies.append(
                    {
                        "mime_type": part["mimeType"],
                        "text": content[:remaining],
                        "truncated": len(content) > remaining,
                    }
                )
                remaining = max(0, remaining - len(content))
    return {
        "id": item["id"],
        "thread_id": item.get("threadId"),
        "labels": item.get("labelIds", []),
        "headers": {
            k: headers[k]
            for k in ("from", "to", "cc", "subject", "date", "message-id", "references")
            if k in headers
        },
        "body": bodies[:10],
        "attachments": attachments,
        "parts_truncated": bool(parts),
    }


def compose(args, original=None):
    body = args.get("body")
    if not isinstance(body, dict) or not set(body) <= {"to", "cc", "subject", "text"}:
        raise ValueError("Provide to, optional cc, subject and text.")
    message = EmailMessage()
    for field in ("to", "cc"):
        values = body.get(field, [])
        if not isinstance(values, list) or len(values) > 50 or (field == "to" and not values):
            raise ValueError("Use an explicit recipient email list.")
        if values:
            message[field] = ", ".join(email(value) for value in values)
    if (
        not isinstance(body.get("subject"), str)
        or not 1 <= len(body["subject"]) <= 300
        or any(c in body["subject"] for c in "\r\n")
    ):
        raise ValueError("Use a short, single-line subject.")
    if not isinstance(body.get("text"), str) or not 1 <= len(body["text"]) <= 32000:
        raise ValueError("Provide message text up to 32000 characters.")
    message["Subject"] = body["subject"]
    message["Message-ID"] = (
        "<" + hashlib.sha256(args["key"].encode()).hexdigest() + "@agent-council.local>"
    )
    if original:
        headers = message_view(original)["headers"]
        if not headers.get("message-id"):
            raise ValueError("The original email has no Message-ID for threading.")
        message["In-Reply-To"] = headers["message-id"]
        message["References"] = (
            headers.get("references", "") + " " + headers["message-id"]
        ).strip()
    message.set_content(body["text"])
    result = {"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()}
    if original:
        result["threadId"] = original["threadId"]
    return result


class Mail:
    def __init__(self, api):
        self.api = api.users()

    def read(self, args):
        action = args["action"]
        if action == "profile":
            return self.api.getProfile(userId="me").execute()
        if action == "search":
            return (
                self.api.messages()
                .list(
                    userId="me",
                    q=args.get("query", ""),
                    maxResults=50,
                    pageToken=args.get("cursor"),
                )
                .execute()
            )
        if action == "drafts":
            return (
                self.api.drafts()
                .list(userId="me", maxResults=50, pageToken=args.get("cursor"))
                .execute()
            )
        if action == "draft_get":
            result = self.api.drafts().get(userId="me", id=args["id"], format="full").execute()
            return {"id": result["id"], "message": message_view(result["message"])}
        return message_view(
            self.api.messages().get(userId="me", id=args["id"], format="full").execute()
        )

    def prepare_write(self, args):
        action = args["action"]
        if action == "send_draft":
            current = self.api.drafts().get(userId="me", id=args["id"], format="raw").execute()
            if not args.get("revision") or args["revision"] != current["message"]["id"]:
                raise ValueError("Read the draft and pass its current message id as revision.")
            # Include the reviewed snapshot, so a concurrent draft edit cannot
            # substitute different recipients/content between read and send.
            snapshot = {"raw": current["message"]["raw"]}
            if current["message"].get("threadId"):
                snapshot["threadId"] = current["message"]["threadId"]
            request = self.api.drafts().send(
                userId="me", body={"id": args["id"], "message": snapshot}
            )
        else:
            original = (
                self.api.messages().get(userId="me", id=args["id"], format="full").execute()
                if action == "reply"
                else None
            )
            body = compose(args, original)
            request = (
                self.api.drafts().create(userId="me", body={"message": body})
                if action == "draft"
                else self.api.messages().send(userId="me", body=body)
            )

        def perform():
            result = request.execute(num_retries=0)
            if action == "draft":
                saved = self.api.drafts().get(userId="me", id=result["id"], format="full").execute()
                return {
                    "id": saved["id"],
                    "message": message_view(saved["message"]),
                    "status": "draft",
                    "verified": True,
                }
            saved = (
                self.api.messages().get(userId="me", id=result["id"], format="metadata").execute()
            )
            if "SENT" not in saved.get("labelIds", []):
                raise ValueError("Sent state was not verified; inspect before any new attempt.")
            return {
                "id": saved["id"],
                "thread_id": saved.get("threadId"),
                "status": "sent",
                "verified": True,
            }

        return perform

"""Google Drive files inside the owner's selected shared folder."""

import base64
import hashlib
import io
import json
import os
import re
import tempfile

FIELDS = "id,name,mimeType,parents,trashed,version,size,md5Checksum,webViewLink,appProperties,capabilities(canEdit,canAddChildren)"


def service_for(home):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    path = home / "google_token.json"
    if path.resolve() != path or path.stat().st_mode & 0o077:
        raise PermissionError("A private existing Drive grant is required.")
    credentials = Credentials.from_authorized_user_file(str(path))
    if not credentials.has_scopes(["https://www.googleapis.com/auth/drive"]):
        raise PermissionError("The existing grant lacks Drive access.")
    if not credentials.valid:
        credentials.refresh(Request())
        data = json.loads(credentials.to_json())
        data["type"] = "authorized_user"
        with tempfile.NamedTemporaryFile(dir=home, delete=False) as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(json.dumps(data).encode())
            output.flush()
            os.fsync(output.fileno())
        os.replace(output.name, path)
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


class Files:
    def __init__(self, service, root):
        self.api, self.root = service.files(), root

    def metadata(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,128}", identity):
            raise ValueError("Use a Drive file or folder ID.")
        return self.api.get(fileId=identity, fields=FIELDS, supportsAllDrives=True).execute()

    def get(self, identity):
        item = self.metadata(identity)
        pending, seen = [item], set()
        for _ in range(64):
            if not pending:
                break
            current = pending.pop()
            if current.get("trashed"):
                continue
            if current["id"] == self.root:
                return item
            if current["id"] in seen:
                continue
            seen.add(current["id"])
            pending.extend(self.metadata(parent) for parent in current.get("parents", []))
        raise PermissionError("The file is outside the shared Drive folder.")

    def read(self, args):
        item = self.get(args.get("id") or self.root)
        action = args["action"]
        if action == "drive_get":
            return item
        if action == "drive_list":
            if item["mimeType"] != "application/vnd.google-apps.folder":
                raise ValueError("Select a folder to list its contents.")
            return self.api.list(
                q=f"'{item['id']}' in parents and trashed=false",
                pageSize=100,
                pageToken=args.get("cursor"),
                fields="nextPageToken,incompleteSearch,files(" + FIELDS + ")",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
        offset = args.get("offset", 0)
        if type(offset) is not int or not 0 <= offset <= 100 * 1024 * 1024:
            raise ValueError("Invalid file offset.")
        mime = item["mimeType"]
        if mime.startswith("application/vnd.google-apps."):
            export = {
                "application/vnd.google-apps.document": "text/plain",
                "application/vnd.google-apps.spreadsheet": "text/csv",
                "application/vnd.google-apps.presentation": "text/plain",
            }.get(mime)
            if export is None:
                raise ValueError("This Google file has no supported text export; use its web link.")
            data = self.api.export_media(fileId=item["id"], mimeType=export).execute()
            chunk, more = data[offset : offset + 32000], offset + 32000 < len(data)
        else:
            request = self.api.get_media(fileId=item["id"], supportsAllDrives=True)
            request.headers["Range"] = f"bytes={offset}-{offset + 31999}"
            chunk = request.execute()
            if len(chunk) > 32000:
                raise ValueError("Drive ignored the bounded download range.")
            more = offset + len(chunk) < int(item.get("size", 0))
        return {
            "id": item["id"],
            "version": item.get("version"),
            "mime_type": mime,
            "base64": base64.b64encode(chunk).decode(),
            "next_offset": offset + len(chunk) if more else None,
        }

    def prepare_write(self, args, *, attachment=None):
        from googleapiclient.http import MediaIoBaseUpload

        name = args.get("title")
        if (
            not isinstance(name, str)
            or not 1 <= len(name.strip()) <= 200
            or any(ord(c) < 32 for c in name)
        ):
            raise ValueError("Use a bounded file or folder name.")
        action = args["action"]
        if action == "drive_update":
            current = self.get(args.get("id"))
            if current.get("version") != args.get("revision"):
                raise ValueError("The file changed; read and reconcile its current version first.")
            if current["mimeType"].startswith("application/vnd.google-apps."):
                raise ValueError(
                    "Use the native document editor for Google-native content; binary replacement is not supported."
                )
        else:
            parent = self.get(args.get("parent") or self.root)
            if parent["mimeType"] != "application/vnd.google-apps.folder":
                raise ValueError("Select an existing parent folder.")
        body = {"name": name.strip()}
        if action == "drive_folder":
            body.update(parents=[parent["id"]], mimeType="application/vnd.google-apps.folder")
            return lambda: self.api.create(
                body=body, fields=FIELDS, supportsAllDrives=True
            ).execute()
        content, encoded = args.get("content"), args.get("base64")
        if attachment is None and (content is None) == (encoded is None):
            raise ValueError("Provide either UTF-8 content or base64 file bytes.")
        data = (
            attachment
            if attachment is not None
            else (
                content.encode()
                if isinstance(content, str)
                else base64.b64decode(encoded, validate=True)
            )
        )
        if attachment is None and len(data) > 65536:
            raise ValueError("Use files up to 64 KiB per shared upload.")
        mime = args.get("mime_type", "text/plain")
        if (
            not isinstance(mime, str)
            or not re.fullmatch(r"[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+", mime)
            or mime.startswith("application/vnd.google-apps.")
        ):
            raise ValueError("Use a real upload MIME type, not a native Google document type.")
        media = MediaIoBaseUpload(
            io.BytesIO(data), mimetype=mime, chunksize=1024 * 1024, resumable=attachment is not None
        )
        if attachment is not None:
            digest = hashlib.sha256(data).hexdigest()
            body["appProperties"] = {"council_sha256": digest}
            # Deduplicate the same bytes in the selected destination, even when
            # Telegram assigned the repeated attachment another local filename.
            existing = (
                self.api.list(
                    q=f"'{parent['id']}' in parents and trashed=false and appProperties has {{ key='council_sha256' and value='{digest}' }}",
                    fields="files(" + FIELDS + ")",
                    pageSize=2,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
                .get("files", [])
            )
            if len(existing) > 1:
                raise ValueError("Multiple existing copies require reconciliation.")
            if existing:
                saved = self.get(existing[0]["id"])
                if (
                    int(saved.get("size", -1)) != len(data)
                    or saved.get("md5Checksum")
                    != hashlib.md5(data, usedforsecurity=False).hexdigest()
                ):
                    raise ValueError("The existing attachment checksum differs.")
                return lambda: {**saved, "verified": True, "reused": True}

        def write():
            if action == "drive_update":
                result = self.api.update(
                    fileId=current["id"],
                    body=body,
                    media_body=media,
                    fields=FIELDS,
                    supportsAllDrives=True,
                ).execute()
            else:
                body["parents"] = [parent["id"]]
                result = self.api.create(
                    body=body, media_body=media, fields=FIELDS, supportsAllDrives=True
                ).execute()
            saved = self.get(result["id"])
            if (
                saved["name"] != name.strip()
                or int(saved.get("size", -1)) != len(data)
                or saved.get("md5Checksum") != hashlib.md5(data, usedforsecurity=False).hexdigest()
            ):
                raise ValueError("Drive upload readback was not confirmed.")
            return {**saved, "verified": True}

        return write

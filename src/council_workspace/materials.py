"""Typed local attachment inspection for the main owner profile."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .attachments import snapshot


def inspect(home, args):
    data, digest = snapshot(home, args.get("path"))
    mode = args.get("mode", "text")
    if mode not in {"text", "preview"}:
        raise ValueError("Use text or preview.")
    number = args.get("page", 1) if mode == "preview" else args.get("offset", 0)
    if type(number) is not int or not (
        1 <= number <= 200 if mode == "preview" else 0 <= number <= 1000000
    ):
        raise ValueError("Invalid page or text offset.")
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("document_reader.py")),
            Path(args["path"]).suffix.lower(),
            mode,
            str(number),
        ],
        input=data,
        capture_output=True,
        timeout=50,
        env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if result.returncode:
        raise ValueError(
            "Could not extract this attachment. It may be encrypted, damaged, unsupported or over the processing limit. Preserve the original and report the unread part."
        )
    if mode == "text":
        return {**json.loads(result.stdout), "sha256": digest}
    if not result.stdout.startswith(b"\x89PNG\r\n\x1a\n") or len(result.stdout) > 20 * 1024 * 1024:
        raise ValueError("PDF preview was not confirmed.")
    folder = home / "cache/images"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"document-{digest}-{number}.png"
    if path.resolve() != path:
        raise PermissionError("Invalid preview destination.")
    with tempfile.NamedTemporaryFile(dir=folder, delete=False) as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(result.stdout)
    os.replace(output.name, path)
    return {
        "image_path": str(path),
        "page": number,
        "sha256": digest,
        "next_action": "Inspect this image with the vision tool; this is not extracted text.",
    }


def register(ctx):
    def available():
        from hermes_constants import get_default_hermes_root, get_hermes_home

        from .client import settings

        try:
            home = get_hermes_home()
            settings(home)
            return home.resolve() == get_default_hermes_root().resolve()
        except (OSError, ValueError, PermissionError):
            return False

    def handler(args, **_kwargs):
        from hermes_constants import get_hermes_home

        try:
            if not available():
                raise PermissionError("Only the main owner assistant reads its local attachments.")
            return json.dumps(inspect(get_hermes_home(), args), ensure_ascii=False)
        except Exception:
            return json.dumps(
                {
                    "error": "Attachment extraction was not confirmed. Preserve the original; do not claim to have read it. Try PDF preview for a scan."
                }
            )

    description = "Inspect owner-supplied attachments in the main profile cache. text reads PDF, DOCX, XLSX, PPTX and UTF-8 text with bounded pagination; follow next_offset and disclose truncated coverage. preview renders a PDF page for vision/OCR. Images use the existing vision tool. Does not execute macros or formulas. Upload originals separately with council_workspace drive_upload."
    ctx.register_tool(
        name="material_read",
        toolset="council_workspace",
        description=description,
        check_fn=available,
        handler=handler,
        schema={
            "name": "material_read",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "mode": {"type": "string", "enum": ["text", "preview"]},
                    "offset": {"type": "integer", "minimum": 0},
                    "page": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    )

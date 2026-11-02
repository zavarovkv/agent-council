"""Offline PDF extraction/preview with the pinned document runtime."""

import tempfile
import unittest
from pathlib import Path

from council_workspace.materials import inspect


class MaterialIntegrationTests(unittest.TestCase):
    def test_real_pdf_text_and_page_preview(self):
        # Minimal PDF with a real page, font and text stream; no external fixture.
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]
        stream = b"BT /F1 18 Tf 20 100 Td (Council fixture) Tj ET"
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
        data = bytearray(b"%PDF-1.4\n")
        offsets = [0]
        for index, body in enumerate(objects, 1):
            offsets.append(len(data))
            data.extend(str(index).encode() + b" 0 obj\n" + body + b"\nendobj\n")
        xref = len(data)
        data.extend(b"xref\n0 6\n0000000000 65535 f \n")
        for offset in offsets[1:]:
            data.extend(f"{offset:010} 00000 n \n".encode())
        data.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            path = home / "cache/documents/fixture.pdf"
            path.parent.mkdir(parents=True)
            path.write_bytes(data)
            result = inspect(home, {"path": str(path)})
            self.assertIn("Council fixture", result["text"])
            self.assertFalse(result["truncated"])
            preview = inspect(home, {"path": str(path), "mode": "preview", "page": 1})
            self.assertTrue(Path(preview["image_path"]).read_bytes().startswith(b"\x89PNG"))
            with self.assertRaises(ValueError):
                inspect(home, {"path": str(path), "mode": "preview", "page": 2})

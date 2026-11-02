"""Offline extraction in a bounded subprocess; never execute document code."""

import io
import json
import re
import resource
import sys
import zipfile
from contextlib import closing
from xml.etree import ElementTree

LIMIT = 1_000_000


def xml(archive, name):
    data = archive.read(name)
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ValueError("Document entities are not supported.")
    return ElementTree.fromstring(data)


def office(data, extension):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        items = archive.infolist()
        if len(items) > 10000 or sum(i.file_size for i in items) > 100 * 1024 * 1024:
            raise ValueError("The expanded document exceeds the extraction limit.")
        names = archive.namelist()
        if extension == ".docx":
            selected = ["word/document.xml"]
        elif extension == ".pptx":
            selected = sorted(
                (
                    n
                    for n in names
                    if re.fullmatch(r"ppt/(slides/slide|notesSlides/notesSlide)\d+\.xml", n)
                ),
                key=lambda n: (n.split("/")[1], int(re.search(r"(\d+)\.xml$", n)[1])),
            )
        else:
            selected = sorted(n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
        shared = []
        if extension == ".xlsx" and "xl/sharedStrings.xml" in names:
            shared = ["".join(e.itertext()) for e in xml(archive, "xl/sharedStrings.xml")]
        parts, length = [], 0
        for name in selected:
            root = xml(archive, name)
            lines = []
            if extension == ".xlsx":
                for cell in root.iter():
                    if cell.tag.rsplit("}", 1)[-1] != "c":
                        continue
                    value = ""
                    formula = ""
                    for child in cell.iter():
                        tag = child.tag.rsplit("}", 1)[-1]
                        if tag in {"v", "t"}:
                            value += child.text or ""
                        elif tag == "f":
                            formula = child.text or ""
                    if cell.get("t") == "s" and value:
                        value = shared[int(value)]
                    lines.append(
                        cell.get("r", "?")
                        + ": "
                        + value
                        + (" [formula: " + formula + "]" if formula else "")
                    )
            else:
                for paragraph in root.iter():
                    if paragraph.tag.rsplit("}", 1)[-1] == "p":
                        lines.append(
                            "".join(
                                e.text or ""
                                for e in paragraph.iter()
                                if e.tag.rsplit("}", 1)[-1] == "t"
                            )
                        )
            text = "\n[" + name + "]\n" + "\n".join(lines)
            parts.append(text)
            length += len(text)
            if length > LIMIT:
                break
        return "\n".join(parts)


def extract(data, extension):
    if extension == ".pdf":
        import pypdfium2 as pdfium

        with closing(pdfium.PdfDocument(data)) as doc:
            parts = []
            size = 0
            for index in range(min(len(doc), 200)):
                with closing(doc[index]) as page, closing(page.get_textpage()) as textpage:
                    text = textpage.get_text_range()
                text = f"\n[Page {index + 1}]\n" + (
                    text or "[No extractable text: inspect page image]"
                )
                parts.append(text)
                size += len(text)
                if size > LIMIT:
                    break
            if len(parts) < len(doc):
                parts.append("\n[Document truncated: additional pages were not extracted]")
            return "\n".join(parts)
    if extension in {".docx", ".pptx", ".xlsx"}:
        return office(data, extension)
    if extension in {".txt", ".md", ".csv", ".tsv", ".json", ".html", ".xml"}:
        return data.decode("utf-8-sig")
    raise ValueError(
        "Unsupported text format; inspect images with vision or preserve the original."
    )


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (40, 40))
    resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
    sys.path.insert(0, "/opt/document-libs")
    data = sys.stdin.buffer.read(50 * 1024 * 1024 + 1)
    if len(data) > 50 * 1024 * 1024:
        raise ValueError("Attachment exceeds the read limit.")
    extension, mode, number = sys.argv[1:]
    if mode == "preview":
        if extension != ".pdf":
            raise ValueError("Only PDF page previews use this action; images use vision directly.")
        import pypdfium2 as pdfium

        with closing(pdfium.PdfDocument(data)) as doc:
            with closing(doc[int(number) - 1]) as page:
                width, height = page.get_size()
                scale = min(2, 2000 / max(width, height))
                page.render(scale=scale).to_pil().save(sys.stdout.buffer, format="PNG")
        return
    text = extract(data, extension)
    truncated = len(text) > LIMIT or "[Document truncated:" in text
    text = text[:LIMIT]
    offset = int(number)
    if not 0 <= offset <= len(text):
        raise ValueError("Invalid text offset.")
    print(
        json.dumps(
            {
                "text": text[offset : offset + 16000],
                "next_offset": offset + 16000 if offset + 16000 < len(text) else None,
                "truncated": truncated,
                "scope": "Text extraction only; embedded images, tracked formatting and spreadsheet calculations require inspection.",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None

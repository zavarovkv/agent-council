import io
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from council_workspace.document_reader import extract


def archive(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        for path, content in files.items():
            bundle.writestr(path, content)
    return output.getvalue()


class DocumentTests(unittest.TestCase):
    def test_office_content_keeps_section_and_cell_coordinates(self):
        doc = archive(
            {
                "word/document.xml": "<document><p><r><t>План проекта</t></r></p><p><r><t>Вторая строка</t></r></p></document>"
            }
        )
        self.assertIn("План проекта\nВторая строка", extract(doc, ".docx"))
        ppt = archive(
            {
                "ppt/slides/slide1.xml": "<slide><p><r><t>Итог</t></r></p></slide>",
                "ppt/notesSlides/notesSlide1.xml": "<notes><p><r><t>Контекст</t></r></p></notes>",
            }
        )
        text = extract(ppt, ".pptx")
        self.assertIn("Итог", text)
        self.assertIn("Контекст", text)
        sheet = archive(
            {
                "xl/sharedStrings.xml": "<sst><si><t>Название</t></si></sst>",
                "xl/worksheets/sheet1.xml": '<worksheet><c r="A1" t="s"><v>0</v></c><c r="B2"><f>1+2</f><v>3</v></c></worksheet>',
            }
        )
        text = extract(sheet, ".xlsx")
        self.assertIn("A1: Название", text)
        self.assertIn("B2: 3 [formula: 1+2]", text)

    def test_document_entities_are_rejected_and_macros_are_never_selected(self):
        doc = archive({"word/document.xml": '<!DOCTYPE x [<!ENTITY a "bad">]><document/>'})
        with self.assertRaises(ValueError):
            extract(doc, ".docx")
        doc = archive(
            {
                "word/document.xml": "<document><p><t>Text</t></p></document>",
                "word/vbaProject.bin": b"never execute",
            }
        )
        self.assertNotIn("never execute", extract(doc, ".docx"))

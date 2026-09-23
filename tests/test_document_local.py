import json
from pathlib import Path
import tempfile
import unittest

from docx import Document
from core.export_service import ExportService
from core.nlp_extractor import NLPExtractor


class DocumentTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.exporter = ExportService(folder.name)
        self.text = 'Әлия, подготовьте отчёт до пятницы. Бюджет < 5 & план > 2.'
        self.meeting = NLPExtractor().process_transcript({'text': self.text})

    def test_docx_contains_readable_complete_transcript(self):
        path = self.exporter.export_to_docx(self.meeting, 'test.docx')
        paragraphs = '\n'.join(p.text for p in Document(path).paragraphs)
        self.assertIn(self.text, paragraphs)

    def test_pdf_escapes_transcript_markup_and_handles_long_rows(self):
        self.meeting['tasks'][0]['task'] = self.text * 90
        path = Path(self.exporter.export_to_pdf(self.meeting, 'test.pdf'))
        self.assertTrue(path.read_bytes().startswith(b'%PDF'))
        self.assertGreater(path.stat().st_size, 1000)

    def test_txt_is_utf8_and_retains_kazakh_letters(self):
        path = Path(self.exporter.export_to_txt(self.meeting, 'test.txt'))
        self.assertIn(self.text, path.read_text(encoding='utf-8-sig'))


if __name__ == '__main__':
    unittest.main()

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from core.save_dialog import ExportSaver


class SaveDialogTests(unittest.TestCase):
    def test_selected_folder_and_unicode_name_are_used_for_every_format(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            destination = root / 'Выбранная папка'
            destination.mkdir()
            saver = ExportSaver()
            for extension in ('txt', 'docx', 'pdf', 'json'):
                source = root / ('Стенограмма.' + extension)
                source.write_bytes('Әлия, подготовьте отчёт.'.encode('utf-8'))
                target = destination / ('Мой протокол.' + extension)
                window = SimpleNamespace(create_file_dialog=lambda *args, **kwargs: (str(target),))
                result = saver.save(window, source)
                self.assertEqual(result['filepath'], str(target))
                self.assertEqual(target.read_bytes(), source.read_bytes())
            self.assertEqual(saver.directory, str(destination))

    def test_cancelling_does_not_create_or_modify_file(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'original.txt'
            source.write_text('unchanged')
            window = SimpleNamespace(create_file_dialog=lambda *a, **kw: None)
            self.assertEqual(ExportSaver().save(window, source), {'cancelled': True})
            self.assertEqual(list(Path(folder).iterdir()), [source])
            self.assertEqual(source.read_text(), 'unchanged')

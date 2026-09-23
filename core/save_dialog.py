"""Save exported documents through the desktop's native Save As dialog."""
from pathlib import Path
import shutil
import threading


class ExportSaver:
    def __init__(self):
        self.directory = str(Path.home() / 'Documents')
        self._lock = threading.Lock()

    def save(self, window, source):
        import webview
        source = Path(source).resolve()
        if not self._lock.acquire(blocking=False):
            raise RuntimeError('Окно сохранения уже открыто.')
        try:
            result = []
            errors = []

            def choose():
                try:
                    result.append(window.create_file_dialog(
                        webview.FileDialog.SAVE, directory=self.directory,
                        save_filename=source.name,
                        file_types=(f'{source.suffix[1:].upper()} (*{source.suffix})',)))
                except Exception as exc:
                    errors.append(exc)

            # WinForms dialogs must run on its STA UI thread, not the HTTP worker.
            native = getattr(window, 'native', None)
            if native is not None and getattr(native, 'InvokeRequired', False):
                from System import Action
                native.Invoke(Action(choose))
            else:
                choose()
            if errors:
                raise errors[0]
            selected = result[0] if result else None
            if not selected:
                return {'cancelled': True}
            target = Path(selected if isinstance(selected, str) else selected[0])
            # The dialog supplies the default extension and overwrite confirmation.
            if not target.suffix:
                target = target.with_suffix(source.suffix)
            if target.resolve() != source:
                shutil.copy2(source, target)
            self.directory = str(target.parent)
            return {'filepath': str(target), 'cancelled': False}
        finally:
            self._lock.release()

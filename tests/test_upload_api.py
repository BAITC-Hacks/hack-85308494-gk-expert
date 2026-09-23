"""Exercise the actual handler over localhost with a stub processing backend."""
import ast
import email
from email import policy
from email.parser import BytesParser
import http.client
from http.server import HTTPServer, SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import urllib.parse
import uuid

PROJECT = Path(__file__).resolve().parent.parent


class TestUploadHandler(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='upload_handler_')
        self.addCleanup(temporary.cleanup)
        namespace = {'RESOURCE_DIR': temporary.name, 'DATA_DIR': temporary.name, '__name__': 'app_http_test'}
        source = ast.parse((PROJECT / 'app.py').read_text(encoding='utf-8-sig'))
        handler = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == 'AppHttpServer')
        safe_modules = {'os', 'sys', 'time', 'json', 'threading', 'typing', 'http', 'urllib', 're', 'uuid', 'socket', 'email', 'io', 'tempfile'}
        imports = []
        for node in source.body:
            if isinstance(node, ast.Import) and all(alias.name.split('.')[0] in safe_modules for alias in node.names):
                imports.append(node)
            elif isinstance(node, ast.ImportFrom) and (node.module or '').split('.')[0] in safe_modules:
                imports.append(node)
        exec(compile(ast.Module(body=imports + [handler], type_ignores=[]), 'app.py', 'exec'), namespace)
        self.uploads = []
        self.failure = None

        def process(path, model='offline'):
            self.uploads.append((path, Path(path).read_bytes(), model))
            if self.failure:
                raise self.failure
            return {'meeting': {'id': 'test', 'tasks': []}}

        namespace['AppHttpServer'].bridge = SimpleNamespace(process_existing_audio=process, echo=lambda params: params)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), namespace['AppHttpServer'])
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=2)

    def request(self, path, body, content_type):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            connection.request('POST', path, body, {'Content-Type': content_type})
            response = connection.getresponse()
            payload = response.read()
            return response.status, json.loads(payload)
        finally:
            connection.close()

    def multipart(self, payload, engine='offline'):
        boundary = b'codex-boundary-12345'
        body = (b'--' + boundary + b'\r\nContent-Disposition: form-data; name="audio"; filename="sample.wav"\r\nContent-Type: audio/wav\r\n\r\n' + payload
                + b'\r\n--' + boundary + b'\r\nContent-Disposition: form-data; name="engine"\r\n\r\n' + engine.encode()
                + b'\r\n--' + boundary + b'--\r\n')
        return self.request('/api/upload_audio', body, 'multipart/form-data; boundary=' + boundary.decode())

    def test_binary_tail_is_preserved(self):
        payload = b'RIFF\x00\x10\x80WAVEbytes-ending-with--\r\n'
        status, data = self.multipart(payload)
        self.assertEqual(status, 200, data)
        self.assertIn('meeting', data)
        self.assertEqual(self.uploads[-1][1], payload)
        self.assertEqual(self.uploads[-1][2], 'offline')

    def test_processing_error_is_json_not_disconnected_socket(self):
        self.failure = RuntimeError('Local model missing')
        status, data = self.multipart(b'audio')
        self.assertIn('Local model missing', data.get('error', ''))

    def test_malformed_json_is_reported(self):
        status, data = self.request('/api/echo', b'{', 'application/json')
        self.assertIn('error', data)
        self.assertGreaterEqual(status, 400)

    def test_missing_file_is_rejected_without_processing(self):
        status, data = self.request('/api/upload_audio', b'--boundary--\r\n', 'multipart/form-data; boundary=boundary')
        self.assertIn('error', data)
        self.assertFalse(self.uploads)

    def test_empty_file_is_rejected(self):
        status, data = self.multipart(b'')
        self.assertIn('error', data)
        self.assertFalse(self.uploads)

    def test_upload_names_cannot_overwrite_previous_audio(self):
        first_status, first = self.multipart(b'first audio')
        second_status, second = self.multipart(b'second audio')
        self.assertIn('meeting', first)
        self.assertIn('meeting', second)
        self.assertNotEqual(self.uploads[0][0], self.uploads[1][0])
        self.assertEqual(Path(self.uploads[0][0]).read_bytes(), b'first audio')


if __name__ == '__main__':
    unittest.main(verbosity=2)

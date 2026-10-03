"""Actual JavaScript client over loopback HTTP; no credentials or external services."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import uvicorn
from researchpilot.api import create_app


class LiveClientTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js required for live JavaScript client integration')
    def test_authenticated_client_over_real_http(self):
        with tempfile.TemporaryDirectory() as tmp, socket.socket() as listener:
            identities = Path(tmp) / 'identities.json'
            identities.write_text(json.dumps([
                {'subject': subject, 'role': role, 'token_sha256': hashlib.sha256(token.encode()).hexdigest()}
                for subject, role, token in [('alice', 'researcher', 'alice-write'),
                    ('alice', 'reader', 'alice-read'), ('bob', 'researcher', 'bob-write')]
            ]), encoding='utf-8')
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
            # The built-in numerical study does not invoke generated-code execution.
            with patch.dict(os.environ, {'RESEARCHPILOT_IDENTITIES_FILE': str(identities),
                    'RESEARCHPILOT_EXECUTOR': 'docker', 'RESEARCHPILOT_WORKERS': '1'}):
                server = uvicorn.Server(uvicorn.Config(create_app(tmp), log_level='error'))
                thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
                thread.start()
                try:
                    deadline = time.monotonic() + 10
                    while not server.started and thread.is_alive() and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertTrue(server.started, 'local API failed to start')
                    result = subprocess.run([shutil.which('node'), str(Path(__file__).with_name('api_live_client.mjs')),
                        f'http://127.0.0.1:{port}', str(identities)], capture_output=True, text=True,
                        encoding='utf-8', errors='replace', timeout=40)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                finally:
                    server.should_exit = True
                    thread.join(timeout=10)
                    self.assertFalse(thread.is_alive(), 'local API failed to stop')

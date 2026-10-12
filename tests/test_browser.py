"""Exercise the real Unix socket bridge with a host browser substitute."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "codex-browser"


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.socket = self.directory / "socket"
        self.log = self.directory / "opened.json"
        opener = self.directory / "browser with spaces"
        opener.write_text(
            "#!/usr/bin/env python3\nimport json, sys\nfrom pathlib import Path\n"
            f"Path({str(self.log)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        )
        opener.chmod(0o755)
        self.env = {**os.environ, "BROWSER": f'"{opener}" --new-tab %s'}
        self.server = subprocess.Popen(
            [str(BRIDGE), "--serve", str(self.socket)], env=self.env,
        )
        self.addCleanup(self.stop_server)
        subprocess.run(
            [str(BRIDGE), "--wait", str(self.socket), str(self.server.pid)],
            check=True,
        )

    def stop_server(self):
        if self.server.poll() is None:
            self.server.terminate()
        self.server.wait(timeout=5)

    def open(self, url):
        return subprocess.run(
            [str(BRIDGE), url],
            check=False,
            env={**os.environ, "CODEX_BROWSER_SOCKET": str(self.socket)},
            capture_output=True, text=True, timeout=10,
        )

    def test_grafana_query_is_forwarded_unchanged(self):
        url = ('https://grafana.dashboard.sekoia.io/explore?panes=%7B%22a%22%3A'
               '%7B%22queries%22%3A%5B%7B%22expr%22%3A%22sum%20by%20'
               '%28topic%2C%20partition%29%20%28kafka_consumergroup_lag%29'
               '%22%7D%5D%7D%7D&schemaVersion=1')
        result = self.open(url)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.log.read_text()), ["--new-tab", url])
        self.assertEqual(self.socket.stat().st_mode & 0o777, 0o600)

    def test_url_shell_characters_are_literal(self):
        marker = self.directory / "executed"
        url = f'https://example.com/?q=$(touch {marker});`touch {marker}`&x="hello"'
        self.assertEqual(self.open(url).returncode, 0)
        self.assertEqual(json.loads(self.log.read_text()), ["--new-tab", url])
        self.assertFalse(marker.exists())

    def test_invalid_url_does_not_kill_relay(self):
        for url in ("file:///etc/passwd", "javascript:alert(1)", "https://example.com/\n"):
            result = self.open(url)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("only accepts HTTP(S)", result.stderr)
        self.assertFalse(self.log.exists())
        self.assertEqual(self.open("https://example.com/").returncode, 0)

    def test_missing_relay_reports_failure(self):
        self.stop_server()
        self.assertNotEqual(self.open("https://example.com/").returncode, 0)

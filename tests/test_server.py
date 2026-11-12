from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from superagent_status.server import StatusServer
from tests.fixtures import build_demo_db

NOW = datetime(2026, 11, 12, 3, 30, tzinfo=timezone.utc)


class StatusServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name)
        build_demo_db(self.state_dir / "orchestrator.sqlite", now=NOW)
        self.server = StatusServer(("127.0.0.1", 0), self.state_dir)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    def _get(self, path: str) -> tuple[int, dict[str, str], bytes]:
        try:
            with urllib.request.urlopen(self.base + path, timeout=3) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def test_index_page(self) -> None:
        status, headers, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("Superagent 状态台", body.decode("utf-8"))
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_status_api(self) -> None:
        status, headers, body = self._get("/api/status")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers["Content-Type"])
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["summary"]["openTickets"], 4)
        self.assertEqual(len(payload["tickets"]), 6)

    def test_health_endpoint(self) -> None:
        status, _, body = self._get("/healthz")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])

    def test_static_assets(self) -> None:
        for path, content_type in (
            ("/static/app.js", "text/javascript"),
            ("/static/styles.css", "text/css"),
            ("/favicon.svg", "image/svg+xml"),
        ):
            with self.subTest(path=path):
                status, headers, body = self._get(path)
                self.assertEqual(status, 200)
                self.assertIn(content_type, headers["Content-Type"])
                self.assertGreater(len(body), 10)

    def test_unknown_path(self) -> None:
        status, _, _ = self._get("/no-such-page")
        self.assertEqual(status, 404)

    def test_missing_state_health_is_503(self) -> None:
        with tempfile.TemporaryDirectory() as empty_dir:
            server = StatusServer(("127.0.0.1", 0), Path(empty_dir))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/healthz"
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(url, timeout=3)
                self.assertEqual(caught.exception.code, 503)
                body = json.loads(caught.exception.read())
                self.assertFalse(body["ok"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

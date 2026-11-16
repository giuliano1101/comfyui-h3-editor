"""Real HTTP fixture on loopback. Only the test replaces the TLS connection;
production destination validation, TLS/SNI and peer pinning have separate tests.
No test can contact a public provider or use a real credential.
"""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.parse import urlsplit

from provider_adapters import MiniMaxAdapter
from provider_jobs import Generation
from provider_transport import SafeHTTPS, TransportError


class FixtureHTTPTest(unittest.TestCase):
    def setUp(self):
        self.received = []
        records = self.received
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def send(self, body, mime="application/json"):
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                records.append((self.command, self.path, self.headers.get("Authorization"), body))
                self.send(json.dumps({"task_id": "fixture123", "base_resp": {"status_code": 0}}).encode())
            def do_GET(self):
                records.append((self.command, self.path, self.headers.get("Authorization"), None))
                if self.path.startswith("/v1/query/"):
                    self.send(json.dumps({"task_id": "fixture123", "status": "Success", "file_id": "456", "base_resp": {"status_code": 0}}).encode())
                elif self.path.startswith("/v1/files/"):
                    self.send(json.dumps({"file": {"file_id": "456", "purpose": "video_generation", "download_url": "https://cdn.example.com/video"}, "base_resp": {"status_code": 0}}).encode())
                elif self.path == "/slow":
                    time.sleep(0.2)
                    self.send(b'{}')
                else:
                    self.send(b'\x00\x00\x00\x18ftypisom' + b'\x00' * 12, "video/mp4")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def transport(self, total=3):
        transport = SafeHTTPS(("api.example.com", "cdn.example.com"), total_timeout=total)
        def connection(host, ip, port, timeout):
            return http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=timeout)
        def destination(url):
            parsed = urlsplit(url)
            return parsed.hostname, 443, "8.8.8.8", parsed.path + ("?" + parsed.query if parsed.query else "")
        return transport, mock.patch("provider_transport.PinnedHTTPS", side_effect=connection), mock.patch.object(transport, "destination", side_effect=destination)

    @mock.patch.dict(os.environ, {"MINIMAX_API_KEY": "synthetic-fixture-key"})
    def test_submit_poll_retrieve_and_download_over_real_local_http(self):
        transport, connection, destination = self.transport()
        adapter = MiniMaxAdapter(host="api.example.com", transport=transport)
        adapter.files = transport
        with connection, destination, tempfile.TemporaryDirectory() as directory:
            task = adapter.submit(Generation("A synthetic shot", 6, "MiniMax-Hailuo-2.3", "768P"))
            result = adapter.poll(task)
            path = Path(directory) / "video.part"
            adapter.download(result.file_id, path)
            self.assertEqual(path.read_bytes()[4:8], b"ftyp")
        self.assertEqual(len(self.received), 4)
        self.assertEqual(self.received[0][3]["duration"], 6)
        self.assertTrue(all(row[2] == "Bearer synthetic-fixture-key" for row in self.received[:3]))
        self.assertIsNone(self.received[3][2])

    def test_wall_clock_deadline_also_limits_response_headers(self):
        transport, connection, destination = self.transport(0.05)
        started = time.monotonic()
        with connection, destination, self.assertRaises(TransportError):
            transport.json("GET", "https://api.example.com/slow")
        self.assertLess(time.monotonic() - started, 1)

from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

from provider_transport import PinnedHTTPS, SafeHTTPS, TransportError, public_ip


def dns(*ips):
    return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ips]


class Response:
    status = 200
    def __init__(self, body=b'{}', **headers):
        self.body, self.headers = body, {"Content-Type": "application/json", **headers}
    def getheader(self, key, default=None):
        return self.headers.get(key, default)
    def read(self, count):
        chunk, self.body = self.body[:count], self.body[count:]
        return chunk


class TransportTest(unittest.TestCase):
    def test_nonpublic_and_translated_ips_refused(self):
        for value in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "172.16.0.1", "192.168.1.1", "0.0.0.0", "100.100.100.200", "168.63.129.16", "192.0.0.9", "192.88.99.1", "224.0.0.1", "192.0.2.1", "::1", "fe80::1", "fc00::1", "ff02::1", "::ffff:8.8.8.8", "64:ff9b::808:808", "2002:0808:0808::1", "not-an-ip"):
            with self.subTest(value=value), self.assertRaises(TransportError):
                public_ip(value)
        self.assertEqual(public_ip("8.8.8.8"), "8.8.8.8")

    def test_url_shape_and_exact_allowlist(self):
        transport = SafeHTTPS(("api.example.com",))
        for url in ("http://api.example.com/a", "https://api.example.com:444/a", "https://api.example.com.evil.org", "https://user:pass@api.example.com/a", "https://api.example.com/#x", "https://api.example.com./", "https://api.example.com\\@evil.org", "https://127.0.0.1", "https://api.example.com/\r\nX:", None):
            with self.subTest(url=url), mock.patch("socket.getaddrinfo") as resolver, self.assertRaises(TransportError):
                transport.destination(url)
            resolver.assert_not_called()

    def test_all_dns_answers_checked_and_connection_uses_pinned_ip(self):
        transport = SafeHTTPS(("api.example.com",))
        with mock.patch("socket.getaddrinfo", return_value=dns("8.8.8.8", "10.0.0.1")), self.assertRaises(TransportError):
            transport.destination("https://api.example.com/a")
        with mock.patch("socket.getaddrinfo", return_value=dns("8.8.8.8")) as resolver:
            self.assertEqual(transport.destination("https://api.example.com/a?q=1"), ("api.example.com", 443, "8.8.8.8", "/a?q=1"))
            resolver.assert_called_once()

    def test_peer_check_and_tls_use_original_hostname(self):
        connection = PinnedHTTPS("api.example.com", "8.8.8.8", 443, 2)
        raw = mock.Mock()
        raw.getpeername.return_value = ("8.8.8.8", 443)
        context = mock.Mock()
        connection._context = context
        with mock.patch("socket.create_connection", return_value=raw) as connect:
            connection.connect()
        connect.assert_called_once_with(("8.8.8.8", 443), 2)
        context.wrap_socket.assert_called_once_with(raw, server_hostname="api.example.com")
        raw.getpeername.return_value = ("1.1.1.1", 443)
        with mock.patch("socket.create_connection", return_value=raw), self.assertRaises(TransportError):
            connection.connect()
        raw.close.assert_called_once()

    def test_slow_dns_is_bounded_without_starting_an_http_connection(self):
        release = threading.Event()
        def slow(*args, **kwargs):
            release.wait(1)
            return dns("8.8.8.8")
        transport = SafeHTTPS(("api.example.com",), total_timeout=0.02)
        started = time.monotonic()
        try:
            with mock.patch("socket.getaddrinfo", side_effect=slow), mock.patch("provider_transport.PinnedHTTPS") as connection, self.assertRaises(TransportError):
                transport.json("GET", "https://api.example.com/")
            connection.assert_not_called()
            self.assertLess(time.monotonic() - started, 0.5)
        finally:
            release.set()

    def test_redirects_and_error_bodies_never_followed_or_echoed(self):
        transport = SafeHTTPS(("api.example.com",))
        for status in (301, 302, 307, 308, 401, 429, 500):
            response = Response(b'secret-key', Location="https://127.0.0.1/")
            response.status = status
            connection = mock.Mock(getresponse=lambda: response)
            with mock.patch.object(transport, "destination", return_value=("api.example.com", 443, "8.8.8.8", "/")), mock.patch("provider_transport.PinnedHTTPS", return_value=connection), self.assertRaises(TransportError) as caught:
                transport.json("POST", "https://api.example.com", token="secret-key", body={})
            self.assertNotIn("secret", str(caught.exception))
            self.assertEqual(connection.request.call_count, 1)
            connection.close.assert_called()

    def test_json_mime_encoding_size_and_truncation(self):
        transport = SafeHTTPS(("api.example.com",), max_json=32)
        for response in (Response(b'{}', **{"Content-Type": "text/html"}), Response(b'x' * 33), Response(b'{}', **{"Content-Length": "1000"}), Response(b'{}', **{"Content-Length": "20"}), Response(b'not-json')):
            connection = mock.Mock()
            with mock.patch.object(transport, "_open", return_value=(connection, response)), self.assertRaises(TransportError):
                transport.json("GET", "https://api.example.com")

    def test_encoded_response_rejected_before_reading(self):
        transport = SafeHTTPS(("api.example.com",))
        response = Response(b'{}', **{"Content-Encoding": "gzip"})
        connection = mock.Mock(getresponse=lambda: response)
        with mock.patch.object(transport, "destination", return_value=("api.example.com", 443, "8.8.8.8", "/")), mock.patch("provider_transport.PinnedHTTPS", return_value=connection), self.assertRaises(TransportError):
            transport.json("GET", "https://api.example.com")

    def test_download_is_bounded_exclusive_and_does_not_carry_credentials(self):
        transport = SafeHTTPS(("cdn.example.com",), max_video=32)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "测试.part"
            data = b'\x00\x00\x00\x18ftypisom' + b'\x00' * 8
            with mock.patch.object(transport, "_open", return_value=(mock.Mock(), Response(data, **{"Content-Type": "video/mp4"}))) as opened:
                transport.download("https://cdn.example.com/signed?secret=x", target)
            opened.assert_called_once_with("GET", "https://cdn.example.com/signed?secret=x")
            self.assertEqual(target.read_bytes(), data)
            with mock.patch.object(transport, "_open", return_value=(mock.Mock(), Response(data))), self.assertRaises(TransportError):
                transport.download("https://cdn.example.com/file", target)
            self.assertEqual(target.read_bytes(), data)

    def test_invalid_or_oversized_media_cleans_up_partial_files(self):
        transport = SafeHTTPS(("cdn.example.com",), max_video=32)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "partial.part"
            for body in (b'<html>secret</html>', b'\x00\x00\x00\x18ftypisom' + b'x' * 40):
                with mock.patch.object(transport, "_open", return_value=(mock.Mock(), Response(body, **{"Content-Type": "video/mp4"}))), self.assertRaises(TransportError):
                    transport.download("https://cdn.example.com/file", target)
                self.assertFalse(target.exists())

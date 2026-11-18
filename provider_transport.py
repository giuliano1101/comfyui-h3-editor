"""HTTPS-only, DNS-pinned provider transport. No redirects, proxies or retries.

Modified 2026-11-17 (GPL-3.0). All response/error bodies stay server-private;
callers see fixed errors. Explicitly approved hosts are still checked for SSRF.
"""
import http.client
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
from threading import Timer
import time
from urllib.parse import urlsplit

_DNS = ThreadPoolExecutor(max_workers=4, thread_name_prefix="h3-provider-dns")


class TransportError(ValueError):
    pass


def public_ip(value):
    try:
        address = ipaddress.ip_address(value)
        if not address.is_global or address.is_multicast or address.is_reserved or address.is_unspecified:
            raise ValueError()
        if isinstance(address, ipaddress.IPv4Address) and (address in ipaddress.ip_network("192.0.0.0/24") or address in ipaddress.ip_network("192.88.99.0/24") or str(address) == "168.63.129.16"):
            raise ValueError()
        if isinstance(address, ipaddress.IPv6Address):
            if address.ipv4_mapped or address.sixtofour or address.teredo or address in ipaddress.ip_network("64:ff9b::/96") or address in ipaddress.ip_network("64:ff9b:1::/48"):
                raise ValueError()
        return str(address)
    except ValueError:
        raise TransportError("non-public destination refused") from None


def approved_host(host):
    if not isinstance(host, str) or len(host) > 253 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
        raise TransportError("invalid approved hostname")
    if ".." in host or "." not in host:
        raise TransportError("invalid approved hostname")
    return host


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, ip, port, timeout):
        super().__init__(host, port=port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self):
        raw = socket.create_connection((self._ip, self.port), self.timeout)
        self._raw = raw
        try:
            if public_ip(raw.getpeername()[0]) != self._ip:
                raise TransportError("connection destination mismatch")
            # TLS SNI and certificate hostname checking use the original host, not the pinned IP.
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


class SafeHTTPS:
    def __init__(self, hosts, *, timeout=20, total_timeout=120, max_json=1 << 20, max_video=256 << 20):
        self.hosts = frozenset(approved_host(h) for h in hosts)
        self.timeout, self.total_timeout = timeout, total_timeout
        self.max_json, self.max_video = max_json, max_video

    def destination(self, url):
        if not isinstance(url, str) or len(url) > 8192 or any(ord(c) <= 32 or ord(c) == 127 for c in url) or "\\" in url:
            raise TransportError("invalid provider URL")
        try:
            parsed = urlsplit(url)
            host, port = parsed.hostname, parsed.port or 443
        except ValueError:
            raise TransportError("invalid provider URL") from None
        if parsed.scheme != "https" or host not in self.hosts or port != 443 or parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise TransportError("destination is not approved HTTPS")
        future = _DNS.submit(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
        try:
            rows = future.result(timeout=min(5, self.timeout, self.total_timeout))
            ips = sorted({public_ip(r[4][0]) for r in rows})
        except (OSError, ValueError, FutureTimeout):
            future.cancel()
            raise TransportError("destination resolution refused") from None
        if not ips:
            raise TransportError("destination resolution refused")
        path = parsed.path or "/"
        return host, port, ips[0], path + ("?" + parsed.query if parsed.query else "")

    def _open(self, method, url, body=None, token=None):
        started = time.monotonic()
        host, port, ip, path = self.destination(url)
        remaining = self.total_timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise TransportError("provider response timed out")
        connection = PinnedHTTPS(host, ip, port, min(self.timeout, remaining))
        connection._h3_deadline = started + self.total_timeout
        timer = Timer(remaining, self._abort, args=(connection,))
        timer.daemon = True
        connection._h3_timer = timer
        timer.start()
        try:
            headers = {"Accept": "application/json" if body is not None or token else "video/mp4,application/octet-stream", "Accept-Encoding": "identity"}
            if token is not None:
                if not isinstance(token, str) or not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
                    raise TransportError("invalid server credential")
                headers["Authorization"] = "Bearer " + token
            if body is not None:
                headers["Content-Type"] = "application/json"
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                # Do not follow Location, read an error body, or echo its URL/message.
                raise TransportError("provider HTTP request refused")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise TransportError("encoded provider response refused")
            return connection, response
        except BaseException:
            self._close(connection)
            raise

    @staticmethod
    def _close(connection):
        timer = getattr(connection, "_h3_timer", None)
        if timer is not None:
            timer.cancel()
        connection.close()

    @staticmethod
    def _abort(connection):
        # A socket timeout alone does not bound a peer slowly dripping headers.
        # Shut down the pinned connection at the wall-clock deadline as well.
        for name in ("sock", "_raw"):
            sock = getattr(connection, name, None)
            if sock:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                    sock.close()
                except OSError:
                    pass
        connection.close()

    def _chunks(self, connection, response, limit):
        length = response.getheader("Content-Length")
        if length is not None:
            try:
                if not 0 <= int(length) <= limit:
                    raise ValueError()
            except ValueError:
                raise TransportError("provider output exceeds size limit") from None
        deadline, size = getattr(connection, "_h3_deadline", None), 0
        if not isinstance(deadline, (int, float)):
            deadline = time.monotonic() + self.total_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TransportError("provider response timed out")
            if connection.sock:
                connection.sock.settimeout(min(self.timeout, remaining))
            chunk = response.read(min(65536, limit - size + 1))
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise TransportError("provider output exceeds size limit")
            yield chunk
        if length is not None and size != int(length):
            raise TransportError("provider response interrupted")

    def json(self, method, url, *, body=None, token=None):
        connection = None
        try:
            encoded = json.dumps(body).encode() if body is not None else None
            connection, response = self._open(method, url, encoded, token)
            if response.getheader("Content-Type", "").split(";", 1)[0].lower() != "application/json":
                raise TransportError("provider did not return JSON")
            result = json.loads(b"".join(self._chunks(connection, response, self.max_json)))
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except Exception:
            raise TransportError("provider JSON request failed") from None
        finally:
            if connection:
                self._close(connection)

    def download(self, url, target):
        """Write exclusively to a caller-controlled staging path; never send an API key."""
        connection, created = None, False
        try:
            connection, response = self._open("GET", url)
            if response.getheader("Content-Type", "").split(";", 1)[0].lower() not in ("video/mp4", "application/octet-stream"):
                raise TransportError("provider output is not MP4")
            with open(target, "xb") as output:
                created = True
                for chunk in self._chunks(connection, response, self.max_video):
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            with open(target, "rb") as source:
                header = source.read(32)
            if len(header) < 12 or header[4:8] != b"ftyp":
                raise TransportError("provider output is not MP4")
            return Path(target)
        except Exception:
            if created:
                Path(target).unlink(missing_ok=True)
            raise TransportError("provider output download failed") from None
        finally:
            if connection:
                self._close(connection)

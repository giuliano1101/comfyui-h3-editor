import os
from types import SimpleNamespace
import unittest
from unittest import mock

from aiohttp import web
from editor_security import Access, Identity


def request(**kwargs):
    transport = mock.Mock()
    transport.get_extra_info.side_effect = lambda key: ("127.0.0.1", 8199) if key == "sockname" else ("127.0.0.1", 4000)
    return SimpleNamespace(transport=transport, scheme="http", host="127.0.0.1:8199", remote="127.0.0.1", secure=False,
                           headers=kwargs.get("headers", {}), cookies=kwargs.get("cookies", {}), method=kwargs.get("method", "GET"))


class SecurityTest(unittest.TestCase):
    def test_session_requires_custom_header(self):
        access = Access()
        with self.assertRaises(web.HTTPForbidden):
            access.bootstrap(request())
        result = access.bootstrap(request(headers={"X-H3-Editor": "1"}))
        self.assertTrue(result.cookies["h3_session"]["httponly"])
        self.assertEqual(result.cookies["h3_session"]["samesite"], "Strict")

    def test_no_cookie_wrong_token_and_cross_origin_are_denied(self):
        access = Access()
        for headers, error in (({}, web.HTTPUnauthorized), ({"Authorization": "Bearer wrong"}, web.HTTPUnauthorized), ({"Origin": "https://evil.example"}, web.HTTPForbidden), ({"Sec-Fetch-Site": "cross-site"}, web.HTTPForbidden)):
            with self.subTest(headers=headers), self.assertRaises(error):
                access.authenticate(request(headers=headers))

    def test_host_port_dns_rebinding_and_remote_peer_denied(self):
        access = Access()
        for host in ("evil.example:8199", "localhost:4444", "127.0.0.1:8199/path", "user@127.0.0.1:8199"):
            req = request()
            req.host = host
            with self.subTest(host=host), self.assertRaises(web.HTTPForbidden):
                access.local(req)
        req = request()
        req.transport.get_extra_info.side_effect = lambda key: ("8.8.8.8", 4000)
        with self.assertRaises(web.HTTPForbidden):
            access.local(req)

    def test_session_expiry_and_csrf(self):
        now = [100]
        access = Access(lambda: now[0])
        response = access.bootstrap(request(headers={"X-H3-Editor": "1"}))
        cookie = {"h3_session": response.cookies["h3_session"].value}
        self.assertTrue(access.authenticate(request(cookies=cookie)).browser)
        with self.assertRaises(web.HTTPForbidden):
            access.authenticate(request(cookies=cookie, method="POST"))
        now[0] += 8 * 3600 + 1
        with self.assertRaises(web.HTTPUnauthorized):
            access.authenticate(request(cookies=cookie))

    @mock.patch.dict(os.environ, {"H3_MCP_TOKEN": "t" * 40, "H3_MCP_PROJECTS": "Test,Other", "H3_MCP_ALLOW_EDITS": "0"})
    def test_mcp_identity_and_project_scope_are_server_facts(self):
        access = Access()
        identity = access.authenticate(request(headers={"Authorization": "Bearer " + "t" * 40}))
        self.assertFalse(identity.browser)
        self.assertFalse(identity.edit)
        access.project(identity, "Test")
        with self.assertRaises(web.HTTPForbidden):
            access.project(identity, "Private")

    def test_approval_is_single_use_scoped_and_expires(self):
        now = [100]
        access = Access(lambda: now[0])
        agent = Identity("mcp-local", False, frozenset(("Test",)), True)
        browser = Identity("editor", True, edit=True)
        payload = {"project": "Test", "revision": 1, "ops": []}
        proposal = access.propose(agent, "ops", payload)
        with self.assertRaises(web.HTTPForbidden):
            access.approve(agent, proposal["id"])
        with self.assertRaises(web.HTTPConflict):
            access.consume(agent, proposal["id"], "ops", payload)
        access.approve(browser, proposal["id"])
        with self.assertRaises(web.HTTPForbidden):
            access.consume(agent, proposal["id"], "ops", {**payload, "revision": 2})
        access.consume(agent, proposal["id"], "ops", payload)
        with self.assertRaises(web.HTTPConflict):
            access.consume(agent, proposal["id"], "ops", payload)
        other = access.propose(agent, "ops", payload)
        now[0] += 301
        with self.assertRaises(web.HTTPConflict):
            access.approve(browser, other["id"])

    def test_rate_limit(self):
        access = Access()
        access.throttle("alice", maximum=1)
        with self.assertRaises(web.HTTPTooManyRequests):
            access.throttle("alice", maximum=1)
        access.throttle("bob", maximum=1)

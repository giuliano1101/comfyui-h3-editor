import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from tools.h3_mcp import BridgeClient, MCP


class MCPTest(unittest.TestCase):
    def test_initialize_and_tools_handshake(self):
        client = mock.Mock()
        client.request.return_value = {"tools": [{"name": "summary"}]}
        server = MCP(client)
        request = lambda method, **kw: server.handle({"jsonrpc": "2.0", "id": 1, "method": method, **kw})
        self.assertIn("error", request("tools/list"))
        result = request("initialize", params={"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}})
        self.assertEqual(result["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(request("tools/list")["result"]["tools"][0]["name"], "summary")
        self.assertEqual(request("ping")["result"], {})
        self.assertIsNone(server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_refused_operation_is_a_tool_error_and_not_retried(self):
        client = mock.Mock()
        client.request.side_effect = ValueError("refused")
        server = MCP(client)
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        response = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "execute_ops", "arguments": {}}})
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(client.request.call_count, 1)

    def test_invalid_jsonrpc_and_params(self):
        server = MCP()
        for request in ([], {}, {"jsonrpc": "1.0", "method": "ping"}, {"jsonrpc": "2.0", "id": True, "method": "ping"}):
            self.assertIn("error", server.handle(request))
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        for params in ({"name": "summary", "arguments": {}, "token": "secret"}, {"name": "summary", "arguments": []}):
            response = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": params})
            self.assertIn("error", response)

    def test_missing_token_never_connects(self):
        with mock.patch.dict(os.environ, {"H3_MCP_TOKEN": ""}), mock.patch("http.client.HTTPConnection") as connection, self.assertRaises(ValueError):
            BridgeClient().request("tools")
        connection.assert_not_called()

    def test_stdio_emits_only_utf8_json_lines(self):
        payload = b'{"jsonrpc":"2.0","id":1,"method":"initialize"}\n{"jsonrpc":"2.0","method":"notifications/initialized"}\n{"jsonrpc":"2.0","id":2,"method":"ping"}\n'
        result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / "tools" / "h3_mcp.py")], input=payload, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[1])["result"], {})
        self.assertNotIn(b"token", result.stdout)

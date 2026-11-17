import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from uuid import uuid4

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer

from editor_bridge import TOOLS, validate
from provider_jobs import JobError
from tools.offline_editor import make_app


class SchemaTest(unittest.TestCase):
    def test_no_render_code_or_arbitrary_path_tool(self):
        for operation in ({"op": "render", "clip": "c1"}, {"op": "add_effect", "fields": {"source": "fetch('secret')"}}, {"op": "update_clip", "clip": "c1", "fields": {"first": {"file": "../secret"}}}):
            with self.assertRaises(JobError):
                validate({"project": "Test", "revision": 1, "ops": [operation]}, TOOLS["propose_ops"])

    def test_bounded_strict_schemas(self):
        valid = {"project": "Test", "revision": 1, "ops": [{"op": "add_clip", "fields": {"action": "A shot"}}]}
        validate(valid, TOOLS["propose_ops"])
        for invalid in ({**valid, "revision": True}, {**valid, "key": "secret"}, {**valid, "ops": []}, {**valid, "ops": valid["ops"] * 21}, {**valid, "ops": [{"op": "add_clip", "fields": {"seconds": float("nan")}}]}):
            with self.assertRaises(JobError):
                validate(invalid, TOOLS["propose_ops"])


class BridgeHTTPTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = make_app(self.temp.name, "_h3_test_" + uuid4().hex)
        self.runtime = self.app["runtime"]
        self.api = self.runtime.api
        self.client = TestClient(TestServer(self.app), cookie_jar=CookieJar(unsafe=True))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        await self.req("GET", "/session")
        response = await self.req("POST", "/projects", {"name": "Test"})
        self.assertEqual(response.status, 200)
        self.project = (await response.json())["project"]

    async def req(self, method, path, body=None, headers=None):
        return await self.client.request(method, "/h3take/api" + path, json=body, headers={"X-H3-Editor": "1", **(headers or {})})

    async def tool(self, name, args, headers=None):
        return await self.req("POST", "/bridge/call", {"name": name, "arguments": args}, headers)

    async def proposal(self, ops=None):
        args = {"project": "Test", "revision": self.api.read_project("Test")["revision"], "ops": ops or [{"op": "add_clip", "fields": {"action": "A shot"}}]}
        response = await self.tool("propose_ops", args)
        self.assertEqual(response.status, 200, await response.text())
        return args, (await response.json())["id"]

    async def approve(self, proposal):
        response = await self.req("POST", "/approvals/approve", {"approval": proposal})
        self.assertEqual(response.status, 200)

    async def add_clip(self):
        args, approval = await self.proposal()
        await self.approve(approval)
        response = await self.tool("execute_ops", {**args, "approval": approval})
        self.assertEqual(response.status, 200, await response.text())
        return self.api.read_project("Test")["clips"][0]["id"]

    async def test_approved_ops_are_atomic_historical_and_cannot_replay(self):
        args, approval = await self.proposal()
        self.assertEqual((await self.tool("execute_ops", {**args, "approval": approval})).status, 409)
        self.assertEqual(self.api.read_project("Test")["clips"], [])
        await self.approve(approval)
        response = await self.tool("execute_ops", {**args, "approval": approval})
        self.assertEqual(response.status, 200)
        self.assertEqual(len(self.api.read_project("Test")["clips"]), 1)
        self.assertEqual((await self.tool("execute_ops", {**args, "approval": approval})).status, 409)
        history = await (await self.tool("history", {"project": "Test"})).json()
        self.assertEqual(len(history["revisions"]), 2)

    async def test_editor_project_listing_load_and_save(self):
        response = await self.req("GET", "/projects")
        self.assertEqual(response.status, 200, await response.text())
        self.assertIn("Test", (await response.json())["projects"])
        data = self.api.read_project("Test")
        data["style"] = "A local test"
        response = await self.req("PUT", "/project/Test", data)
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["project"]["style"], "A local test")

    async def test_chinese_project_creation_save_and_reload_preserve_prompts(self):
        response = await self.req("POST", "/projects", {"name": "中文 验收场景"})
        self.assertEqual(response.status, 200, await response.text())
        made = await response.json()
        self.assertEqual(made["folder"], "中文_验收场景")
        data = made["project"]
        data["style"] = "Live-action, cinematic；保持原始提示词。"
        response = await self.req("PUT", "/project/中文_验收场景", data)
        self.assertEqual(response.status, 200, await response.text())
        response = await self.req("GET", "/project/中文_验收场景")
        self.assertEqual(response.status, 200)
        loaded = await response.json()
        self.assertEqual(loaded["style"], data["style"])
        self.assertEqual(loaded["name"], "中文 验收场景")
        self.assertIn("中文_验收场景", (await (await self.req("GET", "/projects")).json())["projects"])

    async def test_concurrent_editor_change_rejects_stale_agent_revision(self):
        args, approval = await self.proposal()
        await self.approve(approval)
        self.assertEqual((await self.req("POST", "/project/Test/ops", {"revision": 1, "ops": [{"op": "add_clip", "fields": {"action": "User edit"}}]})).status, 200)
        self.assertEqual((await self.tool("execute_ops", {**args, "approval": approval})).status, 409)
        self.assertEqual(self.api.read_project("Test")["clips"][0]["action"], "User edit")

    async def test_invalid_batch_does_not_partially_commit(self):
        ops = [{"op": "add_clip", "fields": {"action": "First"}}, {"op": "update_clip", "clip": "missing", "fields": {"action": "Fail"}}]
        response = await self.tool("propose_ops", {"project": "Test", "revision": 1, "ops": ops})
        self.assertEqual(response.status, 400)
        self.assertEqual(self.api.read_project("Test")["revision"], 1)
        self.assertEqual(self.api.read_project("Test")["clips"], [])

    async def test_restore_creates_new_revision_and_preserves_takes(self):
        clip = await self.add_clip()
        data = self.api.read_project("Test")
        take = {"id": "t1", "file": "fixture.mp4", "frames": 24, "quality": "final", "seed": None}
        self.api.store.accept_take(data, clip, take)
        self.api.commit("Test", data, "fixture")
        args = {"project": "Test", "revision": 3, "target_revision": 2}
        response = await self.tool("propose_restore", args)
        approval = (await response.json())["id"]
        await self.approve(approval)
        response = await self.tool("execute_restore", {**args, "approval": approval})
        self.assertEqual(response.status, 200)
        data = self.api.read_project("Test")
        self.assertEqual(data["revision"], 4)
        self.assertEqual(data["clips"][0]["takes"][0]["id"], "t1")

    async def test_simulated_job_complete_flow_and_fee_confirmation(self):
        clip = await self.add_clip()
        payload = {"project": "Test", "clip": clip, "revision": 2, "provider": "simulation", "model": "simulation", "resolution": "test", "seconds": 4, "prompt": "Private test shot"}
        response = await self.req("POST", "/provider-jobs/preview", payload)
        self.assertEqual(response.status, 200)
        approval = (await response.json())["id"]
        self.assertEqual((await self.req("POST", "/provider-jobs", {"approval": approval, "cost_acknowledged": True})).status, 409)
        await self.approve(approval)
        response = await self.req("POST", "/provider-jobs", {"approval": approval, "cost_acknowledged": True})
        self.assertEqual(response.status, 200, await response.text())
        job = await response.json()
        self.assertEqual(job["state"], "queued")
        self.assertNotIn("Private", json.dumps(job))
        response = await self.req("POST", "/provider-jobs/" + job["id"] + "/poll", {})
        self.assertEqual((await response.json())["state"], "succeeded")
        self.assertEqual((await self.req("POST", "/provider-jobs", {"approval": approval, "cost_acknowledged": True})).status, 409)
        self.assertEqual((await self.req("POST", "/provider-jobs/" + job["id"] + "/stage", {})).status, 409)
        self.assertEqual(self.api.read_project("Test")["revision"], 2)

    async def test_mcp_is_read_only_by_default_and_cannot_use_legacy_routes(self):
        token = "test-token-" + "x" * 32
        headers = {"Authorization": "Bearer " + token}
        with mock.patch.dict(os.environ, {"H3_MCP_TOKEN": token, "H3_MCP_PROJECTS": "Test", "H3_MCP_ALLOW_EDITS": "0"}):
            response = await self.req("GET", "/bridge/tools", headers=headers)
            tools = (await response.json())["tools"]
            self.assertTrue(all(not t["name"].startswith("execute_") for t in tools))
            self.assertEqual((await self.tool("summary", {"project": "Test"}, headers)).status, 200)
            self.assertEqual((await self.tool("summary", {"project": "Private"}, headers)).status, 403)
            self.assertEqual((await self.tool("propose_ops", {"project": "Test", "revision": 1, "ops": [{"op": "add_clip", "fields": {}}]}, headers)).status, 403)
            self.assertEqual((await self.req("POST", "/project/Test/ops", {"ops": []}, headers)).status, 403)
            self.assertEqual((await self.req("POST", "/approvals/approve", {"approval": "anything"}, headers)).status, 403)

    async def test_unauthenticated_cross_origin_unknown_fields_and_size(self):
        self.assertEqual((await self.req("GET", "/providers", headers={"Origin": "https://evil.example"})).status, 403)
        self.assertEqual((await self.tool("summary", {"project": "Test", "token": "secret"})).status, 400)
        self.assertEqual((await self.tool("summary", {"project": "Test", "padding": "x" * 40000})).status, 413)
        async with TestClient(TestServer(make_app(Path(self.temp.name) / "second", "_h3_second_" + uuid4().hex))) as client:
            self.assertEqual((await client.get("/h3take/api/providers")).status, 401)

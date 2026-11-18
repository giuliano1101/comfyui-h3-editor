import copy
import importlib
import os
from pathlib import Path
import unittest
from unittest import mock

import captions
import editing_tools
import numpy as np
import project
from provider_adapters import configured_adapters
from provider_ark import ArkAdapter
from provider_jobs import Generation, JobError, JobManager, SimulatedAdapter
import tempfile
import test_editor_bridge as fixtures


class Contracts(unittest.TestCase):
    def test_currency_cannot_relabel_reserved_charges(self):
        class PaidFixture(SimulatedAdapter):
            price_cents = 100

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "jobs.sqlite3"
            manager = JobManager(
                {"fixture": PaidFixture()},
                db_path=database,
                budget_cents=200,
                currency="CNY",
            )
            job = manager.submit("fixture", "synthetic", 4, cost_acknowledged=True)
            self.assertEqual(job["currency"], "CNY")
            self.assertEqual(manager.usage()["remaining_cents"], 100)
            manager.close()
            with self.assertRaises(JobError):
                JobManager(db_path=database, currency="USD")
            restored = JobManager(db_path=database, currency="CNY")
            self.assertEqual(restored.usage()["reserved_cents"], 100)
            restored.close()

    def test_ark_image_contract_and_no_cdn_credential(self):
        transport = mock.Mock()
        transport.json.return_value = {"id": "task-123"}
        adapter = ArkAdapter(transport=transport)
        request = Generation(
            "paper boat",
            4,
            "doubao-seedance-2-5-260628",
            "720p",
            "image",
            ({"data": "data:image/png;base64,AA", "role": "first_frame"},),
            "1:1",
        )
        with mock.patch.dict(os.environ, {"ARK_API_KEY": "unit-only"}):
            self.assertEqual(adapter.submit(request), "task-123")
            body = transport.json.call_args.kwargs["body"]
            self.assertEqual(body["ratio"], "adaptive")
            self.assertFalse(body["generate_audio"])
            self.assertEqual(body["content"][1]["role"], "first_frame")
            transport.json.return_value = {
                "id": "task-123",
                "status": "succeeded",
                "content": {"video_url": "https://cdn.example.com/signed"},
            }
            self.assertEqual(adapter.poll("task-123").file_id, "task-123")
            adapter.files = mock.Mock()
            adapter.download("task-123", Path("target"))
            adapter.files.download.assert_called_once_with(
                "https://cdn.example.com/signed", Path("target")
            )
        with self.assertRaises(JobError):
            adapter.cancel("task-123")

    def test_model_limits_and_protocol_identity_cannot_expand(self):
        for row in (
            {"id": "minimax", "protocol": "ark-video-v3"},
            {"id": "seedance", "protocol": "minimax-video-v1"},
            {
                "id": "seedance",
                "protocol": "ark-video-v3",
                "model_limits": {
                    "doubao-seedance-2-5-flash-261115": {"resolutions": ["4k"]}
                },
            },
        ):
            with self.assertRaises(JobError):
                configured_adapters({"providers": [row]})
        adapter = ArkAdapter()
        for request in (
            Generation("x", 30, "doubao-seedance-2-0-260128", "720p"),
            Generation("x", 4, "doubao-seedance-2-5-flash-261115", "4k"),
            Generation("x", 4, "doubao-seedance-2-5-260628", "720p", "image"),
        ):
            with self.assertRaises(JobError):
                adapter.validate(request)

    def test_srt_vtt_timecodes_and_ass_injection(self):
        cues = captions.parse("1\n00:00:01,000 --> 00:00:02,500\n你好 {\\pos(0,0)}\n")
        self.assertEqual((cues[0]["start"], cues[0]["end"]), (24, 60))
        script = captions.ass({"subtitles": cues}, 640, 360)
        self.assertNotIn(r"{\pos", script)
        self.assertEqual(
            captions.parse(
                "WEBVTT\n\n00:01.000 --> 00:03.000 align:start\n<b>字幕</b>", "vtt"
            )[0]["text"],
            "字幕",
        )
        for text in (
            "1\n00:99:00,000 --> 00:01:00,000\nx",
            "1\n00:00:02,000 --> 00:00:01,000\nx",
        ):
            with self.assertRaises(ValueError):
                captions.parse(text)

    def test_subtitles_slice_and_alias(self):
        data = project.new_project("Test")
        names = {}
        project.apply_op(
            data,
            {
                "op": "add_subtitle",
                "fields": {"start": 12, "end": 48, "text": "原字"},
                "as": "title",
            },
            names,
        )
        project.apply_op(
            data,
            {"op": "update_subtitle", "subtitle": "$title", "fields": {"text": "改字"}},
            names,
        )
        part = project.slice_project(data, 24, 36)
        self.assertEqual(
            (
                part["subtitles"][0]["start"],
                part["subtitles"][0]["end"],
                part["subtitles"][0]["text"],
            ),
            (0, 12, "改字"),
        )

    def test_audio_layout_preserves_timing_and_rejects_overflow(self):
        data = {
            "sounds": [
                {
                    "id": "s" + str(i),
                    "start": 0,
                    "frames": 48,
                    "lane": 0,
                    "gain": 0.5,
                    "offset": 0.2,
                }
                for i in range(4)
            ]
        }
        before = copy.deepcopy(data)
        ops = editing_tools.tidy_audio(data)
        self.assertEqual(data, before)
        self.assertEqual([o["fields"]["lane"] for o in ops], [1, 2, 3])
        data["sounds"].append(dict(data["sounds"][0], id="fifth"))
        with self.assertRaises(ValueError):
            editing_tools.tidy_audio(data)

    def test_audio_alignment_delay_and_silence(self):
        a = np.random.default_rng(20).normal(size=4000)
        b = np.concatenate([a[500:], np.zeros(500)])
        self.assertEqual(editing_tools.alignment(a, b)["offset_frames"], 12)
        with self.assertRaises(ValueError):
            editing_tools.alignment(np.zeros(1000), np.zeros(1000))


class Integrated(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.BridgeHTTPTest.asyncSetUp
    req = fixtures.BridgeHTTPTest.req
    tool = fixtures.BridgeHTTPTest.tool
    proposal = fixtures.BridgeHTTPTest.proposal
    approve = fixtures.BridgeHTTPTest.approve
    add_clip = fixtures.BridgeHTTPTest.add_clip

    async def test_subtitle_import_is_approved_atomic_and_recoverable(self):
        response = await self.tool(
            "propose_subtitle_file",
            {
                "project": "Test",
                "revision": 1,
                "text": "1\n00:00:00,000 --> 00:00:01,000\n中文验收",
                "format": "srt",
                "replace": False,
            },
        )
        self.assertEqual(response.status, 200, await response.text())
        proposal = await response.json()
        self.assertFalse(self.api.read_project("Test").get("subtitles"))
        await self.approve(proposal["id"])
        response = await self.tool(
            "execute_captions_import",
            {**proposal["payload"], "approval": proposal["id"]},
        )
        self.assertEqual(response.status, 200, await response.text())
        self.assertEqual(
            self.api.read_project("Test")["subtitles"][0]["text"], "中文验收"
        )
        self.assertEqual(self.api.read_revision("Test", 1).get("subtitles", []), [])

    async def test_reviewed_images_change_invalidates_approval_and_are_not_stored(self):
        from PIL import Image

        clip_id = await self.add_clip()
        data = self.api.read_project("Test")
        path = Path(self.api.project_file("Test")).parent / "ref.png"
        Image.new("RGB", (320, 320), "white").save(path)
        subfolder = path.parent.relative_to(
            Path(self.api.folder_paths.get_output_directory())
        ).as_posix()
        data["clips"][0]["first"] = {"file": path.name, "subfolder": subfolder}
        self.api.commit("Test", data, "fixture")
        self.runtime.manager._adapters["minimax"].quotes_cents = {
            "MiniMax-Hailuo-2.3:768P:6:image": 5
        }
        self.runtime.manager._adapters["minimax"].enabled = True
        patcher = mock.patch.dict(os.environ, {"MINIMAX_API_KEY": "unit-only"})
        patcher.start()
        self.addCleanup(patcher.stop)
        args = {
            "project": "Test",
            "revision": data["revision"],
            "clip": clip_id,
            "provider": "minimax",
            "model": "MiniMax-Hailuo-2.3",
            "resolution": "768P",
            "seconds": 6,
            "prompt": "纸船",
            "mode": "image",
            "references": [{"source": "first"}],
        }
        proposal = await self.runtime.generations.preview(
            importlib.import_module(self.api.__package__ + ".editor_security").Identity(
                "editor", True, edit=True
            ),
            args,
        )
        self.assertNotIn("base64", str(proposal))
        self.assertEqual(proposal["estimate_cents"], 5)
        await self.approve(proposal["id"])
        Image.new("RGB", (320, 320), "black").save(path)
        with self.assertRaises(ValueError) as caught:
            await self.runtime.generations.submit(
                importlib.import_module(
                    self.api.__package__ + ".editor_security"
                ).Identity("editor", True, edit=True),
                proposal["id"],
                True,
            )
        self.assertEqual(caught.exception.code, "conflict")

    async def test_conversation_correction_invalidates_old_approval_without_mutation(
        self,
    ):
        plan = {
            "intent": "add_clip",
            "target": "",
            "name": "纸船",
            "text": "",
            "seconds": 4,
            "frames": 0,
            "gain": 1,
            "quality": "draft",
            "message": "拟添加纸船镜头",
        }
        body = {"project": "Test", "revision": 1, "utterance": "添加一个纸船镜头"}
        with mock.patch.object(
            self.runtime.conversation, "interpret", mock.AsyncMock(return_value=plan)
        ):
            first = await (await self.req("POST", "/conversation/preview", body)).json()
            self.assertFalse(first["executed"])
            self.assertEqual(self.api.read_project("Test")["clips"], [])
            second = await self.req(
                "POST",
                "/conversation/preview",
                {
                    **body,
                    "utterance": "不是纸船，改为纸鹤",
                    "previous": first["draft"],
                    "previous_approval": first["proposal"]["id"],
                },
            )
            self.assertEqual(second.status, 200, await second.text())
            self.assertNotIn(first["proposal"]["id"], self.runtime.access.pending)

    async def test_new_reference_schema_accepts_empty_and_rejects_arbitrary_url(self):
        validate = importlib.import_module(
            self.api.__package__ + ".editor_bridge"
        ).validate
        tools = importlib.import_module(self.api.__package__ + ".editor_bridge").TOOLS
        args = {
            "project": "Test",
            "revision": 1,
            "clip": "c1",
            "provider": "simulation",
            "model": "simulation",
            "resolution": "test",
            "prompt": "x",
            "seconds": 4,
            "references": [],
        }
        validate(args, tools["propose_generation"])
        with self.assertRaises(ValueError):
            validate(
                {
                    **args,
                    "references": [
                        {"source": "first", "url": "http://127.0.0.1/private"}
                    ],
                },
                tools["propose_generation"],
            )

    async def test_conversation_generation_uses_explicit_duration_or_selected_default(
        self,
    ):
        clip = await self.add_clip()
        for requested, expected in ((4, 4), (0, 6)):
            plan = {
                "intent": "generate_remote",
                "target": clip,
                "name": "",
                "text": "",
                "seconds": requested,
                "frames": 0,
                "gain": 1,
                "quality": "draft",
                "message": "生成预览",
            }
            body = {
                "project": "Test",
                "revision": self.api.read_project("Test")["revision"],
                "utterance": "预览生成",
                "selected_clip": clip,
                "generation": {
                    "provider": "simulation",
                    "model": "simulation",
                    "resolution": "test",
                    "seconds": 6,
                    "prompt": "面板中的纸船描述",
                    "mode": "text",
                    "references": [],
                },
            }
            with mock.patch.object(
                self.runtime.conversation,
                "interpret",
                mock.AsyncMock(return_value=plan),
            ):
                response = await self.req("POST", "/conversation/preview", body)
                self.assertEqual(response.status, 200, await response.text())
                preview = await response.json()
                self.assertEqual(preview["proposal"]["payload"]["seconds"], expected)
                self.assertEqual(
                    preview["proposal"]["payload"]["prompt"], "面板中的纸船描述"
                )
                self.assertFalse(preview["executed"])
        self.assertEqual(self.runtime.manager.list("editor", "Test"), [])

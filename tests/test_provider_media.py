import asyncio
from dataclasses import replace
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
from uuid import uuid4

from provider_jobs import JobError, Poll, SimulatedAdapter
from tools.offline_editor import make_app

try:
    import av
    import numpy as np
except ImportError:
    av = None


def fixture_video(path):
    with av.open(str(path), "w") as container:
        video = container.add_stream("mpeg4", rate=24)
        video.width, video.height, video.pix_fmt = 64, 48, "yuv420p"
        for index in range(48):
            frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), index * 5 % 255, dtype=np.uint8), format="rgb24")
            for packet in video.encode(frame):
                container.mux(packet)
        for packet in video.encode():
            container.mux(packet)


@unittest.skipIf(av is None, "needs PyAV and numpy for actual MP4 fixtures")
class ProviderMediaTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.app = make_app(self.directory, "_h3_media_" + uuid4().hex)
        self.runtime = self.app["runtime"]
        import importlib
        self.error_type = importlib.import_module(self.runtime.api.__package__ + ".provider_jobs").JobError
        self.addCleanup(self.runtime.manager.close)
        self.api = self.runtime.api
        data = self.api.store.new_project("Test")
        data["sizes"] = {"final": "small"}
        clip, _ = self.api.store.add_clip(data, {"action": "fixture", "seconds": 4})
        self.clip = clip["id"]
        self.api.commit("Test", data, "fixture")
        source = self.directory / "测试素材.mp4"
        fixture_video(source)
        class FixtureAdapter(SimulatedAdapter):
            capabilities = replace(SimulatedAdapter.capabilities, outputs=("video/mp4",))
            def poll(self, handle):
                return Poll("succeeded", "fixture123")
            def download(self, file_id, target):
                shutil.copyfile(source, target)
        self.runtime.manager._adapters["fixture"] = FixtureAdapter()
        job = self.runtime.manager.submit("fixture", "private fixture prompt", 4, cost_acknowledged=True,
                                          owner="editor", project="Test", clip=self.clip)
        self.job = job["id"]
        self.runtime.manager.poll(self.job, "editor")

    async def stage(self):
        return await self.runtime.staging.stage(self.runtime.manager, self.job, "editor")

    async def test_real_mp4_is_validated_and_staged_outside_output(self):
        info = await self.stage()
        self.assertEqual((info["width"], info["height"]), (64, 48))
        self.assertEqual(info["fps"], 24)
        self.assertTrue(self.runtime.manager.get(self.job, "editor")["staged"])
        self.assertEqual(self.api.read_project("Test")["revision"], 1)
        self.assertEqual(list((self.directory / "output").rglob("*.mp4")), [])
        self.assertEqual(await self.stage(), info)

    async def test_new_take_keeps_previous_take_and_duplicate_import_is_refused(self):
        data = self.api.read_project("Test")
        self.api.store.accept_take(data, self.clip, {"id": "old", "file": "old.mp4", "frames": 48, "quality": "final", "seed": None})
        self.api.commit("Test", data, "old-take")
        await self.stage()
        result = await self.runtime.staging.import_take(self.runtime.manager, self.job, "editor", "Test", self.clip, 2)
        self.assertEqual(result["revision"], 3)
        takes = result["project"]["clips"][0]["takes"]
        self.assertEqual(len(takes), 2)
        self.assertEqual(takes[0]["id"], "old")
        self.assertEqual(takes[1]["provider_job"], self.job)
        target = self.directory / "output" / takes[1]["subfolder"] / takes[1]["file"]
        self.assertTrue(target.is_file())
        info = await self.runtime.staging.inspect(target)
        self.assertEqual((info["width"], info["height"]), (640, 352))
        with self.assertRaisesRegex(self.error_type, "already imported"):
            await self.runtime.staging.import_take(self.runtime.manager, self.job, "editor", "Test", self.clip, 3)

    async def test_concurrent_edit_after_conversion_cleans_uncommitted_media(self):
        await self.stage()
        original = self.api.normalize
        async def edited(*args):
            await original(*args)
            data = self.api.read_project("Test")
            data["style"] = "User edited while conversion ran"
            self.api.commit("Test", data, "user-edit")
        with mock.patch.object(self.api, "normalize", side_effect=edited):
            with self.assertRaisesRegex(self.error_type, "revision changed"):
                await self.runtime.staging.import_take(self.runtime.manager, self.job, "editor", "Test", self.clip, 1)
        self.assertTrue(self.runtime.staging.path(self.job).exists())
        self.assertEqual(self.api.read_project("Test")["clips"][0]["takes"], [])
        self.assertEqual(list((self.directory / "output").rglob("*.mp4")), [])
        self.assertEqual(list((self.directory / "output").rglob("*.jpg")), [])

    async def test_late_commit_failure_keeps_media_referenced_by_saved_project(self):
        await self.stage()
        original = self.api.commit
        def late_failure(*args):
            original(*args)
            raise OSError("a late broadcast failure")
        with mock.patch.object(self.api, "commit", side_effect=late_failure), self.assertRaises(OSError):
            await self.runtime.staging.import_take(self.runtime.manager, self.job, "editor", "Test", self.clip, 1)
        take = self.api.read_project("Test")["clips"][0]["takes"][0]
        self.assertTrue((self.directory / "output" / take["subfolder"] / take["file"]).exists())

    async def test_invalid_media_and_interrupted_download_do_not_pollute_project(self):
        adapter, _ = self.runtime.manager.output(self.job, "editor")
        def broken(file_id, target):
            Path(target).write_bytes(b"partial")
            raise OSError("secret-token")
        for download in (broken, lambda file_id, target: Path(target).write_bytes(b"not-a-video")):
            with mock.patch.object(adapter, "download", side_effect=download), self.assertRaises(self.error_type) as caught:
                await self.stage()
            self.assertNotIn("secret", str(caught.exception))
            self.assertFalse(self.runtime.staging.path(self.job).exists())
            self.assertEqual(list(self.runtime.staging.directory.glob("*.part")), [])
            self.assertEqual(self.api.read_project("Test")["revision"], 1)

    async def test_wrong_clip_stale_revision_and_traversal_refused(self):
        await self.stage()
        for clip, revision in (("wrong", 1), (self.clip, 100)):
            with self.assertRaises(self.error_type):
                await self.runtime.staging.import_take(self.runtime.manager, self.job, "editor", "Test", clip, revision)
        for name in ("../x", "x/y", "C:\\outside", ".."):
            with self.assertRaises(self.error_type):
                self.runtime.staging.path(name)

    async def test_export_actual_file_can_be_decoded(self):
        await self.stage()
        await self.runtime.staging.import_take(self.runtime.manager, self.job, "editor", "Test", self.clip, 1)
        data = self.api.read_project("Test")
        response = await self.api.export_snapshot("Test", data)
        import json
        result = json.loads(response.text)
        path = self.directory / "output" / result["subfolder"] / result["filename"]
        self.assertTrue(path.exists())
        info = await self.runtime.staging.inspect(path)
        self.assertGreater(info["seconds"], 1.5)
        with av.open(str(path)) as container:
            decoded = list(container.decode(video=0))
        self.assertGreaterEqual(len(decoded), 47)

    async def test_parallel_exports_reserve_distinct_files_and_keep_existing_output(self):
        import json
        await self.stage()
        await self.runtime.staging.import_take(self.runtime.manager,self.job,'editor','Test',self.clip,1)
        data=self.api.read_project('Test')
        directory=Path(self.api.root())/'Test'/'exports';directory.mkdir(parents=True,exist_ok=True)
        existing=directory/'Test_001.mp4';existing.write_bytes(b'preserved-existing-export')
        replies=await asyncio.gather(self.api.export_snapshot('Test',data),self.api.export_snapshot('Test',data))
        files=[json.loads(reply.text)['filename'] for reply in replies]
        self.assertEqual(len(set(files)),2)
        self.assertEqual(existing.read_bytes(),b'preserved-existing-export')
        for file in files:
            with av.open(str(directory/file)) as container:
                self.assertGreaterEqual(len(list(container.decode(video=0))),47)

    async def test_failed_export_removes_only_its_reserved_partial_output(self):
        await self.stage()
        await self.runtime.staging.import_take(self.runtime.manager,self.job,'editor','Test',self.clip,1)
        with mock.patch.object(self.api,'run',mock.AsyncMock(side_effect=RuntimeError('fixture failure'))),self.assertRaises(RuntimeError):
            await self.api.export_snapshot('Test',self.api.read_project('Test'))
        self.assertEqual(list((Path(self.api.root())/'Test'/'exports').glob('*.mp4')),[])

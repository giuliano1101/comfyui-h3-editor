"""Controlled staging and revision-aware take import (GPL-3.0, 2026-11-17).

The staging directory is outside ComfyUI output and never served as static media.
Partial files and failed imports do not mutate the project or replace an old take.
"""
import asyncio
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

try:
    from .provider_jobs import JobError, identifier
except ImportError:
    from provider_jobs import JobError, identifier


class Staging:
    def __init__(self, directory, api):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.api, self.locks = api, {}

    def path(self, job_id):
        identifier(job_id, "job")
        target = self.directory / (job_id + ".mp4")
        if target.resolve().parent != self.directory or target.is_symlink():
            raise JobError("invalid staging path")
        return target

    async def inspect(self, path):
        """Probe in a bounded subprocess. Decode samples, not just an extension/MIME."""
        code = (
            "import av,json,sys; "
            "c=av.open(sys.argv[1], options={'protocol_whitelist':'file,pipe'}); "
            "s=c.streams.video[0]; "
            "w,h=s.codec_context.width,s.codec_context.height; "
            "fps=float(s.average_rate or 0); "
            "duration=float(c.duration or 0)/1000000; "
            "assert 16<=w<=4096 and 16<=h<=4096 and 1<=fps<=120 and 0<duration<=60; "
            "next(c.decode(s)); "
            "print(json.dumps(dict(width=w,height=h,fps=fps,seconds=duration))); c.close()"
        )
        process = await asyncio.create_subprocess_exec(sys.executable, "-c", code, str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            out, _ = await asyncio.wait_for(process.communicate(), 20)
            if process.returncode or len(out) > 2048:
                raise ValueError()
            return json.loads(out)
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise JobError("invalid or unreadable staged video") from None

    async def stage(self, manager, job_id, owner):
        async with self.locks.setdefault(job_id, asyncio.Lock()):
            target = self.path(job_id)
            if target.exists():
                info = await self.inspect(target)
                manager.mark_staged(job_id, owner)
                return info
            adapter, file_id = manager.output(job_id, owner)
            part = target.with_suffix("." + uuid4().hex + ".part")
            try:
                # Adapter transport has connect/read/total/size bounds. Cancellation of the
                # request cannot orphan a thread that later publishes a staged result.
                task = asyncio.create_task(asyncio.to_thread(adapter.download, file_id, part))
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    await task
                    raise
                info = await self.inspect(part)
                if not 12 <= part.stat().st_size <= 256 << 20:
                    raise JobError("staged output outside size limits")
                os.replace(part, target)
                manager.mark_staged(job_id, owner)
                return info
            except JobError:
                raise
            except Exception:
                raise JobError("output download failed; project unchanged", "download_failed", 502) from None
            finally:
                part.unlink(missing_ok=True)

    async def import_take(self, manager, job_id, owner, folder, clip_id, revision):
        job = manager.get(job_id, owner)
        if job["project"] != folder or job["clip"] != clip_id or not job["staged"]:
            raise JobError("staged output does not belong to this clip", "conflict", 409)
        source = self.path(job_id)
        if not source.is_file():
            raise JobError("stage this output before importing", "unavailable", 409)
        api, store = self.api, self.api.store
        # Validate before the expensive normalization and again at commit time.
        async with api.lock(folder):
            data = api.read_project(folder)
            if data.get("revision", 0) != revision:
                raise JobError("project revision changed; review before importing", "conflict", 409)
            clip = store.find_clip(data, clip_id)
            if clip.get("link"):
                raise JobError("remote outputs cannot satisfy local H3 linked-frame contracts")
            if any(t.get("provider_job") == job_id for c in data["clips"] for t in c.get("takes", [])):
                raise JobError("this output is already imported", "conflict", 409)
            width, height, refined = store.render_size(data, "final")
            if refined:
                width, height = store.refine_size(data)
        await self.inspect(source)
        take_id = store.new_id("t")
        dest = Path(api.root()) / folder / "clips"
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / (clip_id + "_" + take_id + ".mp4")
        # Keep incomplete files outside output. Windows requires the handle closed for rename.
        temporary = self.directory / (uuid4().hex + ".mp4")
        moved, committed = False, False
        try:
            await asyncio.wait_for(api.normalize(str(source), str(temporary), width, height, "contain", False, 4), 120)
            await self.inspect(temporary)
            os.replace(temporary, target)
            moved = True
            info = await asyncio.wait_for(api.probe_file(str(target)), 60)
            take = dict(id=take_id, clip=clip_id, file=target.name,
                        subfolder=f"{store.ROOT}/{folder}/clips", poster=info["poster"],
                        frames=info["frames"], quality="final", seed=None, size=[width, height],
                        provider=job["provider"], provider_model=job["model"], provider_job=job_id)
            if info.get("strip"):
                take["strip"] = info["strip"]
            async with api.lock(folder):
                data = api.read_project(folder)
                if data.get("revision", 0) != revision:
                    raise JobError("project revision changed; staged output kept for retry", "conflict", 409)
                if any(t.get("provider_job") == job_id for c in data["clips"] for t in c.get("takes", [])):
                    raise JobError("this output is already imported", "conflict", 409)
                store.accept_take(data, clip_id, take)
                api.commit(folder, data, "provider-import")
                committed = True
            return {"revision": data["revision"], "project": data, "take": take_id}
        finally:
            temporary.unlink(missing_ok=True)
            if moved and not committed:
                # The incumbent commit writes project.json before history/broadcast.
                # A late failure must never delete media a saved revision already names.
                current = api.read_project(folder)
                committed = any(t.get("id") == take_id for c in current.get("clips", []) for t in c.get("takes", []))
            if moved and not committed:
                for suffix in (".mp4", ".jpg", ".strip.jpg", ".peaks.json"):
                    target.with_suffix(suffix).unlink(missing_ok=True)

"""One approval/quote/material path for browser and scoped MCP callers."""

import asyncio
import base64
import hashlib
import io
from pathlib import Path

from .provider_jobs import JobError, identifier

GENERATION_FIELDS = (
    "provider",
    "model",
    "resolution",
    "prompt",
    "seconds",
    "project",
    "clip",
    "revision",
)


class GenerationService:
    def __init__(self, api, access, manager, staging):
        self.api, self.access, self.manager, self.staging = (
            api,
            access,
            manager,
            staging,
        )

    def images(self, folder, data, clip, selectors, mode):
        if not isinstance(selectors, list) or len(selectors) > 4:
            raise JobError("at most four reviewed images are supported")
        from PIL import Image

        root = Path(self.api.project_file(folder)).resolve().parent
        media, disclosure, total = [], [], 0
        for selector in selectors:
            if not isinstance(selector, dict) or set(selector) - {"source", "id"}:
                raise JobError("invalid project image selection")
            source = selector.get("source")
            if source in ("first", "last"):
                asset = clip.get(source)
            elif source == "cast":
                member = next(
                    (c for c in data.get("cast", []) if c["id"] == selector.get("id")),
                    None,
                )
                asset = member if member and member.get("file") else None
            else:
                raise JobError("only current-project image selections are accepted")
            if not isinstance(asset, dict) or not asset.get("file"):
                raise JobError("selected project image is missing")
            path = Path(
                self.api.output_path(asset.get("subfolder", ""), asset["file"])
            ).resolve()
            if (
                not path.is_relative_to(root)
                or not path.is_file()
                or path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp")
            ):
                raise JobError("selected image is outside this project or unsupported")
            size = path.stat().st_size
            total += size
            if not 0 < size <= 4 << 20 or total > 8 << 20:
                raise JobError("reviewed images exceed the local upload limit")
            try:
                with path.open("rb") as source_file:
                    raw = source_file.read((4 << 20) + 1)
                if not raw or len(raw) > 4 << 20 or len(raw) != size:
                    raise ValueError()
                with Image.open(io.BytesIO(raw)) as image:
                    width, height = image.size
                    if (
                        not min(width, height) > 300
                        or max(width, height) > 6000
                        or not 0.4 <= width / height <= 2.5
                    ):
                        raise ValueError()
                    mime = {
                        "JPEG": "image/jpeg",
                        "PNG": "image/png",
                        "WEBP": "image/webp",
                    }[image.format]
                    image.verify()
            except Exception:
                raise JobError(
                    "project image is unreadable or outside provider dimensions"
                ) from None
            digest = hashlib.sha256(raw).hexdigest()
            role = "first_frame" if mode == "image" else "reference_image"
            media.append(
                {
                    "role": role,
                    "data": "data:"
                    + mime
                    + ";base64,"
                    + base64.b64encode(raw).decode("ascii"),
                }
            )
            disclosure.append(
                {"name": path.name, "sha256": digest, "bytes": len(raw), "role": role}
            )
        return tuple(media), disclosure

    async def prepare(self, identity, body):
        if (
            not isinstance(body, dict)
            or set(GENERATION_FIELDS) - body.keys()
            or set(body) - set((*GENERATION_FIELDS, "mode", "references"))
        ):
            raise JobError("request does not match generation schema")
        folder = identifier(body["project"], "project")
        if self.api.store.slug(folder) != folder:
            raise JobError("use the exact project folder identifier")
        self.access.project(identity, folder)
        async with self.api.lock(folder):
            data = self.api.read_project(folder)
            if (
                type(body["revision"]) is not int
                or data["revision"] != body["revision"]
            ):
                raise JobError("project revision changed", "conflict", 409)
            clip = self.api.store.find_clip(data, identifier(body["clip"], "clip"))
            if clip.get("link"):
                raise JobError(
                    "remote generation cannot fulfill local H3 linked-frame contracts"
                )
            mode = body.get("mode", "text")
            media, disclosure = await asyncio.to_thread(
                self.images, folder, data, clip, body.get("references", []), mode
            )
            ratio = data.get("format", "16:9")
        adapter, generation = self.manager.validate(
            body["provider"],
            body["prompt"],
            body["seconds"],
            body["model"],
            body["resolution"],
            mode=mode,
            media=media,
            ratio=ratio,
        )
        payload = {**body, "mode": mode, "references": body.get("references", [])}
        return payload, adapter, generation, disclosure

    async def preview(self, identity, body):
        payload, adapter, generation, disclosure = await self.prepare(identity, body)
        payload["reference_disclosure"] = disclosure
        payload["approved_quote_cents"] = adapter.quote(generation)
        result = self.access.propose(identity, "provider_submit", payload)
        result.update(
            cost_notice=adapter.capabilities.cost_notice,
            estimate_cents=adapter.quote(generation),
            simulated=adapter.capabilities.simulated,
            currency=self.manager.currency,
        )
        self.access.pending[result["id"]].update(
            {
                k: result[k]
                for k in ("cost_notice", "estimate_cents", "simulated", "currency")
            }
        )
        return result

    async def submit(self, identity, approval, acknowledged):
        proposal = (
            self.access.pending.get(approval) if isinstance(approval, str) else None
        )
        if (
            not proposal
            or proposal["action"] != "provider_submit"
            or acknowledged is not True
        ):
            raise JobError(
                "review and confirm the cost notice first", "confirmation_required", 409
            )
        saved = proposal["payload"]
        body = {
            k: v
            for k, v in saved.items()
            if k not in ("reference_disclosure", "approved_quote_cents")
        }
        payload, adapter, generation, disclosure = await self.prepare(identity, body)
        if disclosure != saved["reference_disclosure"]:
            raise JobError("reviewed image changed; preview again", "conflict", 409)
        if adapter.quote(generation) != saved["approved_quote_cents"]:
            raise JobError("price changed; preview again", "conflict", 409)
        async with self.api.lock(payload["project"]):
            if (
                self.api.read_project(payload["project"])["revision"]
                != payload["revision"]
            ):
                raise JobError(
                    "project revision changed; preview again", "conflict", 409
                )
            self.access.consume(identity, approval, "provider_submit", saved)
        self.access.throttle(identity.name, "submit", 10)
        return await asyncio.to_thread(
            self.manager.submit,
            payload["provider"],
            payload["prompt"],
            payload["seconds"],
            model=payload["model"],
            resolution=payload["resolution"],
            mode=generation.mode,
            media=generation.media,
            ratio=generation.ratio,
            owner="editor",
            cost_acknowledged=True,
            idempotency_key=approval,
            project=payload["project"],
            clip=payload["clip"],
        )

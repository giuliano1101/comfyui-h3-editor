import asyncio
import json
import os
import site
import sys
import time
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
base = repo / ".acceptance"
site.addsitedir(str(base / "client-packages"))
from mcp import ClientSession  # noqa: E402 - isolated dependency path set above
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

folder = "H3_综合验收_20261118"
phase = sys.argv[1]
private = json.loads((base / "mcp-private.json").read_text())
report_path = base / "actual-mcp.json"
report = (
    json.loads(report_path.read_text(encoding="utf-8"))
    if report_path.exists()
    else {
        "client": "official Python MCP SDK 2.3.0 / stdio",
        "server": "real isolated ComfyUI 0.45.0",
        "checks": [],
    }
)


def record(name, **facts):
    report["checks"].append({"name": name, **facts})
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def result(client, name, args):
    r = await client.call_tool(name, args)
    if r.is_error:
        return {"error": True, "detail": r.content[0].text}
    return json.loads(r.content[0].text)


async def connect(kind, work):
    env = {
        k: v
        for k, v in os.environ.items()
        if not any(
            x in k.upper() for x in ("KEY", "TOKEN", "SECRET", "PASSWORD", "PROXY")
        )
    }
    env.update(
        H3_MCP_TOKEN=private[kind], H3_EDITOR_PORT="8190", PYTHONIOENCODING="utf-8"
    )
    params = StdioServerParameters(
        command=str(base / "python/python.exe"),
        args=["-B", str(repo / "tools/h3_mcp.py")],
        env=env,
    )
    with (base / "mcp-client-stderr.log").open("a", encoding="utf-8") as log:
        async with stdio_client(params, errlog=log) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=180) as client:
                init = await client.initialize()
                await work(client, init)


async def inspect(client, init):
    tools = (await client.list_tools()).tools
    blocked = await result(client, "summary", {"project": "H3_综合验收_20261118_2"})
    write = await result(
        client,
        "propose_ops",
        {
            "project": folder,
            "revision": 6,
            "ops": [{"op": "add_clip", "fields": {"name": "不应添加"}}],
        },
    )
    record(
        "readonly_connection",
        protocol=init.protocol_version,
        tool_count=len(tools),
        write_tools_hidden=not any(
            t.name.startswith(("propose_", "execute_")) for t in tools
        ),
        out_of_scope_rejected=bool(blocked.get("error")),
        mutation_rejected=bool(write.get("error")),
    )


async def approved(client, init):
    summary = await result(client, "summary", {"project": folder})
    revision = summary["revision"]
    clip = summary["clips"][0]["id"]
    args = {"project": folder, "revision": revision}
    if phase == "edit":
        name = "ops"
        args["ops"] = [
            {
                "op": "update_clip",
                "clip": clip,
                "fields": {"name": "Agent 已确认的合成片段"},
            }
        ]
    elif phase == "generation":
        name = "generation"
        args.update(
            clip=clip,
            provider="simulation",
            model="simulation",
            resolution="test",
            seconds=4,
            prompt="合成纸船，无外部调用",
            mode="text",
            references=[],
        )
    elif phase == "restore":
        name = "restore"
        args["target_revision"] = 6
    elif phase == "export":
        name = "export"
    elif phase == "cancel":
        name = "provider_cancel"
        job = json.loads((base / "mcp-simulation-job.json").read_text())
        args["job"] = job["id"]
    else:
        raise ValueError("unsupported phase")
    resume = len(sys.argv) > 2 and sys.argv[2] == "resume"
    proposal = (
        json.loads(
            (base / ("mcp-ready-" + phase + ".json")).read_text(encoding="utf-8")
        )
        if resume
        else await result(client, "propose_" + name, args)
    )
    if resume:
        args = proposal["payload"]
    if proposal.get("error"):
        raise RuntimeError(str(proposal))
    execute = "execute_" + name
    execute_args = (
        {"project": folder, "approval": proposal["id"], "cost_acknowledged": True}
        if name == "generation"
        else {**args, "approval": proposal["id"]}
    )
    denied = {"error": True} if resume else await result(client, execute, execute_args)
    if not denied.get("error"):
        raise RuntimeError("mutation executed before approval")
    record(
        phase + "_preview", revision=revision, approval_required=True, proposal=proposal
    )
    ready = base / ("mcp-ready-" + phase + ".json")
    signal = base / ("mcp-continue-" + phase + ".txt")
    if signal.exists() and not resume:
        raise RuntimeError(
            "old signal exists; preserve evidence and choose a new phase"
        )
    ready.write_text(
        json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("WAITING_FOR_BROWSER_APPROVAL " + phase, flush=True)
    start = time.monotonic()
    while not resume and not signal.exists():
        if time.monotonic() - start > 240:
            raise TimeoutError("browser approval not observed")
        await asyncio.sleep(0.5)
    applied = await result(client, execute, execute_args)
    if applied.get("error"):
        raise RuntimeError(str(applied))
    duplicate = await result(client, execute, execute_args)
    record(
        phase + "_execute", result=applied, replay_rejected=bool(duplicate.get("error"))
    )
    if phase == "generation":
        (base / "mcp-simulation-job.json").write_text(
            json.dumps(applied), encoding="utf-8"
        )
    if phase == "edit":
        # A competing client uses the now-stale revision; it must not get an applicable proposal.
        conflict = await result(client, "propose_ops", args)
        record(
            "concurrent_revision", stale_proposal_rejected=bool(conflict.get("error"))
        )
    print("VERIFIED " + phase, flush=True)


async def concurrent_case(first, init):
    async def with_second(second, second_init):
        snapshot = await result(first, "summary", {"project": folder})
        revision = snapshot["revision"]
        clip = snapshot["clips"][0]
        args = {
            "project": folder,
            "revision": revision,
            "ops": [
                {
                    "op": "update_clip",
                    "clip": clip["id"],
                    "fields": {"name": clip["name"]},
                }
            ],
        }
        proposals = await asyncio.gather(
            result(first, "propose_ops", args), result(second, "propose_ops", args)
        )
        if any(p.get("error") for p in proposals):
            raise RuntimeError("concurrent previews failed")
        ready = base / "mcp-ready-concurrent.json"
        signal = base / "mcp-continue-concurrent.txt"
        if signal.exists():
            raise RuntimeError(
                "preserve old concurrent evidence; choose a fresh isolation checkout"
            )
        ready.write_text(
            json.dumps(proposals, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            "WAITING_FOR_BROWSER_APPROVAL concurrent (two identical no-op renames)",
            flush=True,
        )
        started = time.monotonic()
        while not signal.exists():
            if time.monotonic() - started > 240:
                raise TimeoutError("browser approval not observed")
            await asyncio.sleep(0.5)
        outcomes = await asyncio.gather(
            *[
                result(client, "execute_ops", {**args, "approval": proposal["id"]})
                for client, proposal in zip((first, second), proposals)
            ]
        )
        after = await result(first, "summary", {"project": folder})
        passed = (
            sum(not o.get("error") for o in outcomes) == 1
            and after["revision"] == revision + 1
            and after["clips"][0]["name"] == clip["name"]
        )
        record(
            "concurrent_actual_clients",
            two_stdio_clients=True,
            before_revision=revision,
            after_revision=after["revision"],
            outcomes=outcomes,
            passed=passed,
        )
        if not passed:
            raise RuntimeError("concurrent revision invariant failed")
        print("VERIFIED concurrent", flush=True)

    await connect("write", with_second)


asyncio.run(
    connect(
        "read" if phase == "inspect" else "write",
        inspect
        if phase == "inspect"
        else concurrent_case
        if phase == "concurrent"
        else approved,
    )
)

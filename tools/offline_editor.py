"""Isolated HTTP/browser harness. Not ComfyUI, no model loading or generation.

Exercises the real editor routes/project/media code with synthetic folder_paths.
Never install this as a second plugin. All files stay in the specified workspace.
"""
import argparse
import importlib
import os
from pathlib import Path
import sys
import types


def load_api(directory, package="_h3_offline"):
    directory = Path(directory).resolve()
    source = Path(__file__).resolve().parents[1]
    module = types.ModuleType(package)
    module.__path__ = [str(source)]
    sys.modules[package] = module
    folders = types.ModuleType("folder_paths")
    for kind in ("output", "input", "user"):
        path = directory / kind
        path.mkdir(parents=True, exist_ok=True)
        setattr(folders, "get_" + kind + "_directory", lambda p=path: str(p))
    folders.folder_names_and_paths = {}
    folders.get_filename_list = lambda kind: []
    previous = sys.modules.get("folder_paths")
    sys.modules["folder_paths"] = folders
    try:
        api = importlib.import_module(package + ".editor_api")
    finally:
        if previous is None:
            sys.modules.pop("folder_paths", None)
        else:
            sys.modules["folder_paths"] = previous
    api.broadcast = lambda *args: None
    api.ensure_watcher = lambda: None
    api.available_models = lambda: set()
    api.refmods_status = lambda: {"installed": False, "files": []}
    api.refmod_files = lambda: None
    api.node_definitions = lambda: {}
    api.patch_list = lambda: []
    api.installed_patches = lambda: {}
    api.lora_files = lambda: []
    api.encoder_choices = lambda _: {"text_encoders": [], "projections": {}, "vaes": [], **api.store.encoder_status(set())}
    api.sampling_choices = lambda: {"samplers": [], "schedulers": []}
    return api


def make_app(directory, package="_h3_offline", config=None):
    from aiohttp import web
    api = load_api(directory, package)
    provider_api = importlib.import_module(package + ".provider_api")
    runtime = provider_api.Runtime(api, Path(directory) / "private", config or {})
    app = web.Application(client_max_size=16 << 20)
    app["runtime"] = runtime
    for method, path, handler in api.ROUTES + provider_api.ROUTES:
        async def call(request, handler=handler):
            if handler == "page":
                runtime.access.local(request, navigation=True)
                return await api.page(request)
            return await runtime.dispatch(request, handler)
        app.router.add_route(method, path, call)
    app.router.add_static("/h3take/editor-files/", api.PAGE)
    async def view(request):
        sub = request.query.get("subfolder", "")
        file = request.query.get("filename", "")
        return web.FileResponse(api.output_path(sub, file))
    app.router.add_get("/view", view)
    async def websocket(request):
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        async for _ in socket:
            pass
        return socket
    app.router.add_get("/ws", websocket)
    async def cleanup(app):
        runtime.manager.close()
    app.on_cleanup.append(cleanup)
    return app


def main():
    from aiohttp import web
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default=".ui-test")
    parser.add_argument("--port", type=int, default=8199)
    args = parser.parse_args()
    app = make_app(args.directory)
    web.run_app(app, host="127.0.0.1", port=args.port, access_log=None)


if __name__ == "__main__":
    main()

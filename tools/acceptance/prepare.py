"""Prepare an isolated Windows ComfyUI; never install into the production tree.

No pip installs, model downloads, launches, or production mutations are performed.
Modified 2026-11-18, GPL-3.0.
"""

import argparse
import json
from pathlib import Path
import shutil
import subprocess

import yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production", default="G:/ComfyUI_windows_portable")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    production = Path(args.production).resolve()
    base = repo / ".acceptance"
    if (
        production == base
        or production.is_relative_to(base)
        or base.is_relative_to(production)
    ):
        raise ValueError("Isolation and production roots must be separate")
    source_python = production / "python_embeded"
    source_comfy = production / "ComfyUI"
    if (
        not (source_comfy / "main.py").is_file()
        or not (source_python / "python.exe").is_file()
    ):
        raise ValueError("Expected an existing portable ComfyUI installation")
    base.mkdir(exist_ok=True)
    if not (base / "ComfyUI").exists():
        subprocess.run(
            [
                "git",
                "clone",
                "--no-hardlinks",
                str(source_comfy),
                str(base / "ComfyUI"),
            ],
            check=True,
        )
    runtime = base / "python"
    runtime.mkdir(exist_ok=True)
    for file in source_python.iterdir():
        if (
            file.is_file()
            and file.suffix in (".exe", ".dll", ".pyd", ".zip")
            and not (runtime / file.name).exists()
        ):
            shutil.copy2(file, runtime / file.name)
    pth = next(source_python.glob("python3*._pth")).name
    standard_library = next(source_python.glob("python3*.zip")).name
    (base / "packages").mkdir(exist_ok=True)
    (runtime / pth).write_text(
        f"{standard_library}\n.\n../packages\n../../.test-deps\n{source_python.as_posix()}/Lib/site-packages\n../ComfyUI\nimport site\n",
        encoding="utf-8",
    )
    plugin = base / "ComfyUI/custom_nodes/comfyui-h3-editor"
    shutil.copytree(
        repo,
        plugin,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            ".git",
            ".acceptance",
            ".ui-test*",
            ".test-deps",
            ".test-tmp",
            "__pycache__",
            ".taste-skill",
            ".ruff_cache",
        ),
    )
    gguf = base / "ComfyUI/custom_nodes/ComfyUI-GGUF"
    if not gguf.exists() and (source_comfy / "custom_nodes/ComfyUI-GGUF").is_dir():
        shutil.copytree(
            source_comfy / "custom_nodes/ComfyUI-GGUF",
            gguf,
            ignore=shutil.ignore_patterns(".git", "__pycache__"),
        )
    kinds = (
        "diffusion_models",
        "text_encoders",
        "vae",
        "loras",
        "checkpoints",
        "latent_upscale_models",
        "upscale_models",
        "vae_approx",
        "clip_projections",
    )
    config = {
        "production_read_only": {
            "base_path": str(source_comfy / "models"),
            **{kind: kind + "/" for kind in kinds},
        }
    }
    (base / "extra_model_paths.yaml").write_text(
        yaml.safe_dump(config), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "comfyui": str(base / "ComfyUI"),
                "plugin": str(plugin),
                "production_changed": False,
            }
        )
    )


if __name__ == "__main__":
    main()

"""Bounded SRT/WebVTT subtitles on the existing project's frame clock (GPL-3.0)."""

import html
import re
from uuid import uuid4

FPS = 24
MAX_CUES = 300


def clean(fields):
    if not isinstance(fields, dict) or set(fields) - {"start", "end", "text"}:
        raise ValueError("字幕字段只支持 start、end、text")
    result = dict(fields)
    for key in ("start", "end"):
        if key in result and (
            type(result[key]) is not int or not 0 <= result[key] <= 1000000
        ):
            raise ValueError("字幕时间须为有效时间线帧")
    if "text" in result:
        text = result["text"]
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text) > 1000
            or any(ord(c) < 32 and c not in "\n\t" for c in text)
        ):
            raise ValueError("字幕文字须为 1 至 1000 字符")
        result["text"] = text.strip()
    return result


def stamp(text):
    parts = text.replace(",", ".").split(":")
    if len(parts) not in (2, 3):
        raise ValueError("字幕时间码格式无效")
    seconds = float(parts[-1])
    minutes = int(parts[-2])
    hours = int(parts[0]) if len(parts) == 3 else 0
    if not 0 <= seconds < 60 or not 0 <= minutes < 60 or not 0 <= hours <= 11:
        raise ValueError("字幕时间码越界")
    return round((hours * 3600 + minutes * 60 + seconds) * FPS)


def parse(text, kind="srt", offset=0):
    if (
        not isinstance(text, str)
        or len(text) > 500000
        or kind not in ("srt", "vtt")
        or type(offset) is not int
    ):
        raise ValueError("请提供小于 500 KB 的 SRT 或 WebVTT 字幕")
    rows = []
    for block in re.split(
        r"\n\s*\n",
        text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip(),
    ):
        lines = block.splitlines()
        if not lines or lines[0].startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue
        index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if index is None:
            raise ValueError("字幕块缺少时间码")
        match = re.fullmatch(
            r"\s*([\d:.,]+)\s*-->\s*([\d:.,]+)(?:\s+.*)?", lines[index]
        )
        if not match:
            raise ValueError("字幕时间码格式无效")
        cue = clean(
            {
                "start": stamp(match[1]) + offset,
                "end": stamp(match[2]) + offset,
                "text": html.unescape(
                    re.sub(r"<[^>]*>", "", "\n".join(lines[index + 1 :]))
                ),
            }
        )
        if cue["end"] <= cue["start"]:
            raise ValueError("字幕结束须晚于开始")
        cue["id"] = "sub" + uuid4().hex[:12]
        rows.append(cue)
        if len(rows) > MAX_CUES:
            raise ValueError("每次最多导入 300 条字幕")
    if not rows:
        raise ValueError("未找到有效字幕")
    return rows


def apply(project, op):
    rows = project.setdefault("subtitles", [])
    kind = op["op"]
    if kind == "add_subtitle":
        cue = clean(op.get("fields", {}))
        if (
            set(cue) != {"start", "end", "text"}
            or cue["end"] <= cue["start"]
            or len(rows) >= MAX_CUES
        ):
            raise ValueError("字幕需要完整时间和文字，且总数不超过 300")
        cue["id"] = "sub" + uuid4().hex[:12]
        rows.append(cue)
        return {"subtitle": cue["id"]}
    cue = next((r for r in rows if r["id"] == op.get("subtitle")), None)
    if cue is None:
        raise ValueError("找不到指定字幕")
    if kind == "remove_subtitle":
        rows.remove(cue)
    else:
        result = {**cue, **clean(op.get("fields", {}))}
        if result["end"] <= result["start"]:
            raise ValueError("字幕结束须晚于开始")
        cue.update(result)
    return {"subtitle": cue["id"]}


def ass(project, width, height):
    def time(frame):
        n = round(frame / FPS * 100)
        return f"{n // 360000}:{n // 6000 % 60:02}:{n // 100 % 60:02}.{n % 100:02}"

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,{max(14, round(height * 0.055))},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,0,2,20,20,{max(12, round(height * 0.055))},1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    rows = []
    for cue in project.get("subtitles", []):
        clean(cue if "id" not in cue else {k: v for k, v in cue.items() if k != "id"})
        # Override braces/backslash controls are rendered as visible text, never ASS code.
        text = (
            cue["text"]
            .replace("\\", "＼")
            .replace("{", "｛")
            .replace("}", "｝")
            .replace("\n", r"\N")
        )
        rows.append(
            f"Dialogue: 0,{time(cue['start'])},{time(cue['end'])},Default,,0,0,0,,{text}"
        )
    return header + "\n".join(rows) + "\n"


def filter_path(path):
    return str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")

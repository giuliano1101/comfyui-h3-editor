"""Measure the joins of a timeline: how exactly a take carries on from the frames it held. The same files give the same numbers.

A take that extends or bridges another holds 39 of its frames. Those frames are then in both files: at the end of the
source (or its start), and at the start of the new take (or its end). The two copies should be the same picture and the
same sound, so how far apart they are says how good the join is, with nothing to judge by eye:

  latent   the two takes' saved latents over the held frames (see holds.py), compared number for number. `picture` is
           the largest difference: under EXACT the hold is exactly what the source was sampled as. `sound` is how
           many of the held sound's steps are, and `of` how many were held: the editor softens the few next to the
           new material on purpose, so those are regenerated a little. None: a take has no saved latents, or the held
           frames are not on the latent's grid, so they went through the decoder and the encoder again.
  psnr     how alike the two decoded pictures are, frame by frame over the held frames, in dB at the file's own size.
           Even an exact hold is not decoded alike everywhere: a decoder draws the last frames of a file with nothing
           after them and the first with nothing before them. `level` is the median of the middle third, where both
           files have frames either side. `edge` is how many frames at the source's end fall more than EDGE dB under
           it: how far the decoder's edge reaches.
  best     the held frame where a cut from one file to the other would show least (both neighbours agree best):
           `psnr` there, `now` at the place the timeline cuts now, and `deeper`, how many frames further inside the
           held frames than the cut now that place is (negative: nearer their edge). A link with a handover already
           cuts a few frames inside them.
  darker   how much darker (+) or brighter (-) the new take's copy is, in percent. Encoding a hold again cost
           1.5 to 3% each time, and every take carried on from that.
  softer   how much fine detail the new take's copy lost, in percent (detail.fine, the sharpest edges).
  sound    the two copies of the held sound: `alike`, their correlation (1 is the same wave), `lag`, how many
           milliseconds the new take's copy runs late (+) or early (-), and `click`, the step in the wave where the
           timeline cuts from one file to the other, against the steps around it. Under 3 is no click.
  cut      the cut the timeline plays, scored as cuts.py scores any cut.

Usage: python joins.py PROJECT_FOLDER             every join on the project's timeline
       python joins.py --json PROJECT_FOLDER
       python joins.py A.mp4 a_first B.mp4 b_first      one join: the held frames start at a_first in A and b_first in B
"""
import json
import os
import subprocess
import sys

import numpy as np

try:
    from . import cuts, detail, holds
except ImportError:
    import cuts
    import detail
    import holds

FPS = 24
HOLD = 39
RATE = 48000
EDGE = 2.0        # dB under the level at which a frame counts as touched by the decoder's edge
LAG = 0.03        # seconds either way the sound's lag is looked for
EXACT = 1e-4      # two saved latents this close are the same: what is left is float16 rounding near zero


def plain(args):
    return args


def size(path, command=plain):
    return detail.size(path, command)


def luma(path, first, count, width, height, command=plain):
    """count frames of a file from frame first on, as luma in 0..1 at width x height. Exact frames, not a seek."""
    last = int(first) + int(count) - 1
    raw = subprocess.run(command(["ffmpeg", "-v", "error", "-i", str(path), "-an",
                                  "-vf", "select='between(n\\,%d\\,%d)',setpts=N/FRAME_RATE/TB,scale=%d:%d:flags=area" % (first, last, width, height),
                                  "-frames:v", str(count), "-f", "rawvideo", "-pix_fmt", "gray", "-"]),
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, height, width).astype(np.float64) / 255.0


def sound(path, first, count, command=plain):
    """The sound under count frames of a file from frame first on, as one channel at RATE. None for a silent file."""
    got = subprocess.run(command(["ffmpeg", "-v", "error", "-i", str(path), "-vn",
                                  "-af", "atrim=start=%.6f:duration=%.6f" % (first / float(FPS), count / float(FPS)),
                                  "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"]), capture_output=True, check=False)
    wave = np.frombuffer(got.stdout, dtype=np.float32).astype(np.float64)
    return wave if len(wave) else None


def psnr(a, b):
    mse = float(np.mean((a - b) ** 2))
    return 99.0 if mse < 1e-12 else min(99.0, 10.0 * np.log10(1.0 / mse))


def cut_index(count, tail, over):
    """Which held frame the timeline cuts before: frames under it play from one file, the rest from the other.

    With no handover the cut is at the edge of the held frames: after the last one where the source's end is held,
    before the first one where its start is. over is how many frames inside them it sits.
    """
    return count - over if tail else over


def picture(ours, theirs, tail=True, over=0):
    """Compare two copies of the same held frames. ours is the source's, theirs the new take's.

    tail says the source's copy is the end of its file (an extension after it, the first side of a bridge). Otherwise
    it is the start (an extension before it, the second side of a bridge). over is the link's handover.
    """
    count = min(len(ours), len(theirs))
    if count < 3:
        return None
    each = [psnr(ours[i], theirs[i]) for i in range(count)]
    third = count // 3
    level = float(np.median(each[third:count - third]))
    from_edge = each[::-1] if tail else each
    edge = 0
    while edge < count and from_edge[edge] < level - EDGE:
        edge += 1
    # A cut before frame k shows one file's frame k - 1 and then the other's frame k: both have to agree there.
    pair = [min(each[k - 1], each[k]) for k in range(1, count)]
    k = int(np.argmax(pair)) + 1
    at = cut_index(count, tail, over)
    now = pair[at - 1] if 1 <= at < count else (each[-1] if tail else each[0])
    mean_ours, mean_theirs = float(np.mean(ours[:count])), float(np.mean(theirs[:count]))
    fine_ours = [f for f in (detail.fine(y) for y in ours[third:count - third:4]) if f]
    fine_theirs = [f for f in (detail.fine(y) for y in theirs[third:count - third:4]) if f]
    softer = None
    if fine_ours and fine_theirs:
        softer = round(100.0 * (1.0 - float(np.mean(fine_theirs)) / float(np.mean(fine_ours))), 2)
    return {"psnr": [round(p, 2) for p in each], "level": round(level, 2), "edge": edge,
            "best": {"deeper": (at - k) if tail else (k - at), "psnr": round(pair[k - 1], 2), "now": round(now, 2)},
            "darker": round(100.0 * (1.0 - mean_theirs / mean_ours), 2) if mean_ours > 1e-6 else None, "softer": softer}


def lag_of(ours, theirs, most=LAG):
    """How many seconds theirs runs behind ours, and their correlation once lined up."""
    n = min(len(ours), len(theirs))
    reach = int(most * RATE)
    if n < 4 * reach:
        return None, None
    a, b = ours[:n] - np.mean(ours[:n]), theirs[:n] - np.mean(theirs[:n])
    core = a[reach:n - reach]
    norm = np.sqrt(float(np.dot(core, core)))
    if norm < 1e-9:
        return None, None
    best, at = -2.0, 0
    for shift in range(-reach, reach + 1):
        other = b[reach + shift:n - reach + shift]
        size_other = np.sqrt(float(np.dot(other, other)))
        if size_other < 1e-9:
            continue
        value = float(np.dot(core, other)) / (norm * size_other)
        if value > best:
            best, at = value, shift
    return at / float(RATE), best


def click(before, after, around=0.02):
    """The step where two waves are joined end to start, against the usual step between samples near it."""
    reach = int(around * RATE)
    if before is None or after is None or len(before) < reach or len(after) < reach:
        return None
    near = np.concatenate([np.diff(before[-reach:]), np.diff(after[:reach])])
    usual = float(np.sqrt(np.mean(near ** 2)))
    if usual < 1e-7:
        return None
    return round(abs(float(after[0]) - float(before[-1])) / usual, 2)


def latent(a_hold, a_first, a_total, b_hold, b_first, b_total, count=HOLD, tail=True):
    """The saved latents of the same held frames in two takes: the picture's largest difference, and how many of the
    sound's steps are the same, counted from the side the held frames do not meet new material at."""
    ours = holds.part(a_hold, a_first, a_first + count, a_total)
    theirs = holds.part(b_hold, b_first, b_first + count, b_total)
    if ours is None or theirs is None or ours["samples"].shape != theirs["samples"].shape:
        return None
    found = {"picture": float((ours["samples"] - theirs["samples"]).abs().max())}
    if "audio" in ours and "audio" in theirs and ours["audio"].shape == theirs["audio"].shape:
        apart = (ours["audio"] - theirs["audio"]).abs().amax(dim=tuple(range(ours["audio"].dim() - 1))).tolist()
        found["sound"] = next((n for n, value in enumerate(apart if tail else apart[::-1]) if value > EXACT), len(apart))
        found["of"] = len(apart)
    return found


def measure(a, a_first, b, b_first, count=HOLD, tail=True, command=plain, a_total=None, b_total=None, over=0):
    """One join. The held frames are a's from a_first and b's from b_first; a is the source, b the take that held them.

    a_total and b_total are the files' frame counts, when they are known; otherwise they are counted. over is the
    link's handover: how many frames inside the held frames the timeline cuts from one file to the other.
    """
    a_size, b_size = size(a, command), size(b, command)
    width, height = a_size[0], a_size[1]
    ours = luma(a, a_first, count, width, height, command)
    theirs = luma(b, b_first, count, width, height, command)
    at = cut_index(count, tail, over)
    found = {"size": [list(a_size[:2]), list(b_size[:2])], "over": over, "picture": picture(ours, theirs, tail, over)}
    a_total, b_total = a_total or frames_of(a, command), b_total or frames_of(b, command)
    found["latent"] = latent(holds.hold_path(a), a_first, a_total, holds.hold_path(b), b_first, b_total, count, tail)
    wave_a, wave_b = sound(a, a_first, count, command), sound(b, b_first, count, command)
    if wave_a is not None and wave_b is not None:
        late, alike = lag_of(wave_a, wave_b)
        if tail:      # the timeline plays a up to the cut, then b from the same held frame on
            before, after = sound(a, a_first, at, command), sound(b, b_first + at, 2, command)
        else:         # it plays b up to the cut, then a
            lead = min(2, b_first + at)
            before, after = sound(b, b_first + at - lead, lead, command), sound(a, a_first + at, count - at, command)
        found["sound"] = {"alike": None if alike is None else round(alike, 4),
                          "lag": None if late is None else round(1000.0 * late, 2), "click": click(before, after)}
    else:
        found["sound"] = None
    if tail:
        found["cut"] = cuts.score_cut(a, a_first + at, b, b_first + at, command)
    else:
        found["cut"] = cuts.score_cut(b, b_first + at, a, a_first + at, command)
    return found


def frames_of(path, command=plain):
    """Frames in a file, counted without decoding them."""
    got = subprocess.run(command(["ffmpeg", "-v", "error", "-stats", "-i", str(path), "-map", "0:v:0", "-c", "copy", "-f", "null", "-"]),
                         capture_output=True, check=False)
    text = got.stderr.decode(errors="replace")
    at = text.rfind("frame=")
    if at < 0:
        raise ValueError("could not count the frames of %s" % path)
    return int(text[at + 6:].split()[0])


def active_take(clip):
    takes = clip.get("takes") or []
    for take in takes:
        if take.get("id") == clip.get("active"):
            return take
    return takes[-1] if takes else None


def project_joins(project):
    """Every join the timeline plays between a linked clip and a clip it holds frames of.

    Each is {"name", "mode", "tail", "a": (file, subfolder, first), "b": (file, subfolder, first), "count", "at",
    "totals", "over"}: a is the source and b the linked take, first is where the held frames start in each, totals are
    the two takes' frame counts, at is the timeline frame of the cut, and over is the link's handover. A join whose
    source has been trimmed or rendered again since, or moved away, is left out: the files no longer show it.
    """
    clips = {c["id"]: c for c in project.get("clips", [])}
    items = {i["id"]: i for i in project.get("timeline", [])}
    found = []
    for item in sorted(project.get("timeline", []), key=lambda i: i["start"]):
        clip = clips.get(item["clip"])
        link = (clip or {}).get("link")
        take = active_take(clip) if clip else None
        if not link or not take:
            continue
        hold, over = int(link.get("hold") or HOLD), int(link.get("handover") or 0)
        for key, tail in (("a", link["mode"] != "extend_before"), ("b", False)):
            side = link.get(key)
            source = items.get((side or {}).get("item"))
            if not side or not source:
                continue
            other = active_take(clips.get(source["clip"]) or {})
            whole = source["out"] == side["last"] - over if tail else source["in"] == side["first"] + over
            if not other or other["file"] != side["file"] or not whole:
                continue
            length = max(1, int(round((source["out"] - source["in"]) / float(source.get("speed") or 1))))
            if tail and source["start"] + length == item["start"]:
                found.append({"name": clip.get("name"), "mode": link["mode"], "tail": True, "count": hold, "at": item["start"],
                              "totals": (other.get("frames"), take.get("frames")), "over": over,
                              "a": (side["file"], side.get("subfolder"), side["last"] - hold),
                              "b": (take["file"], take.get("subfolder"), 0)})
            elif not tail:
                mine = max(1, int(round((item["out"] - item["in"]) / float(item.get("speed") or 1))))
                if item["start"] + mine == source["start"]:
                    found.append({"name": clip.get("name"), "mode": link["mode"], "tail": False, "count": hold, "at": source["start"],
                                  "totals": (other.get("frames"), take.get("frames")), "over": over,
                                  "a": (side["file"], side.get("subfolder"), side["first"]),
                                  "b": (take["file"], take.get("subfolder"), int(take.get("frames") or 0) - hold)})
    return found


def measure_project(folder, command=plain):
    """Measure every join of the project in folder, a folder of the output folder's h3take_projects."""
    with open(os.path.join(folder, "project.json"), encoding="utf-8") as f:
        project = json.load(f)
    output = os.path.dirname(os.path.dirname(os.path.abspath(folder)))

    def path(file, subfolder):
        return os.path.join(output, subfolder or "", file)

    rows = []
    for join in project_joins(project):
        a, b = path(*join["a"][:2]), path(*join["b"][:2])
        row = {"name": join["name"], "mode": join["mode"], "at": join["at"]}
        if not os.path.exists(a) or not os.path.exists(b):
            row["error"] = "a file is missing"
        else:
            try:
                row.update(measure(a, join["a"][2], b, join["b"][2], join["count"], join["tail"], command, *join["totals"],
                                   over=join["over"]))
            except (ValueError, subprocess.CalledProcessError) as error:
                row["error"] = str(error)[:200]
        rows.append(row)
    return rows


def line(row):
    if row.get("error"):
        return "%-28s %-13s %s" % ((row.get("name") or "")[:28], row.get("mode", ""), row["error"])
    p, s, held = row.get("picture") or {}, row.get("sound") or {}, row.get("latent")
    exact = "pixels" if held is None else ("exact" if held["picture"] <= EXACT else "%.3g" % held["picture"])
    if held is not None and held.get("sound") is not None:
        exact += " +%d/%d" % (held["sound"], held["of"])
    best = p.get("best") or {}

    def show(value, form):
        return "-" if value is None else form % value

    return "%-28s %-13s %-13s over %s  level %5s  edge %2s  now %5s  best %5s %3s deeper  darker %6s  softer %6s  sound %6s lag %6s click %5s  cut %s" % (
        (row.get("name") or "")[:28], row.get("mode", ""), exact, row.get("over", 0), show(p.get("level"), "%.1f"), show(p.get("edge"), "%d"),
        show(best.get("now"), "%.1f"), show(best.get("psnr"), "%.1f"), show(best.get("deeper"), "%+d"),
        show(p.get("darker"), "%+.2f%%"), show(p.get("softer"), "%+.1f%%"), show(s.get("alike"), "%.3f"),
        show(s.get("lag"), "%+.1f"), show(s.get("click"), "%.1f"), show((row.get("cut") or {}).get("score"), "%s"))


def main(argv):
    try:
        from . import media
        command = media.command
    except ImportError:
        try:
            import media
            command = media.command
        except ImportError:
            command = plain
    as_json = "--json" in argv
    args = [a for a in argv if a != "--json"]
    if len(args) == 1:
        rows = measure_project(args[0], command)
    elif len(args) == 4:
        rows = [dict({"name": os.path.basename(args[2]), "mode": "by hand", "at": None},
                     **measure(args[0], int(args[1]), args[2], int(args[3]), command=command))]
    else:
        print(__doc__)
        return 2
    lines = [json.dumps(rows, indent=1)] if as_json else [line(row) for row in rows] or ["no joins on this timeline"]
    for text in lines:
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace") + os.linesep.encode())     # a clip's name can hold any character
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

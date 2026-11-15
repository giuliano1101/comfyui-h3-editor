"""Score the cut between two pieces of video, from their pixels alone. The same files always give the same numbers.

A cut reads well in two cases: the picture carries on as if nothing happened (a continuation), or it changes enough to
be a new shot. What reads badly is the middle: the same shot with the subject popped to another pose, a jump cut.

For the last frames of the outgoing piece and the first frames of the incoming one, at 96x54:
  step   how much the picture changes across the cut, where it changes most (95th percentile of the luma
         difference over 6x6 blocks, 0..1), so a pose that pops in a small part of the frame still counts
  motion the same measure from frame to frame just before and just after the cut (the median)
  alike  how alike the two pictures are in layout (correlation of the blurred luma, -1..1)
  shift  how far the picture moved, as a fraction of its width (phase correlation)
  colour the largest change of a colour channel's mean (0..1)

kind   continuous  step is no more than twice the motion around it, plus 0.02
       jump        the same shot (alike >= 0.5) but not continuous: the worse the more alike
       new shot    a different picture
score  0..100. continuous 90-100, new shot 70-100, jump 0-60 (up to 90 when it is only just over the line). A colour change costs up to 20 on a continuous cut.

score_movement() reads how the camera moves on each side of a cut (pan, tilt, push-in or pull-back, from the
optical flow of the whole frame) and says whether the move carries across it, stops, starts or reverses.
score_parts() takes that move out and looks at what is left, part by part of the frame: something that moves on
one side of a cut and is still on the other, such as rain behind a window or a beam of light.

The editor scores every cut of a timeline with score_cut(); GET /h3take/api/project/{name}/cuts returns them.

scan() does the same for a finished file, where nothing says where the cuts are: it finds every place the picture
changes more than its own motion explains, scores it as above, and reports a shot of STRAY frames or fewer between
two cuts as a stray frame (score 0): a frame of another picture left in by mistake. A fade or a dip to black changes
a little on every frame, so it is not a cut and is not reported.

inside() looks into one generated take, which should be one shot: a cut in it, or a fade to another picture (the
model sometimes ends a take by dissolving into a different room), is reported with the frame where it starts. It also
reports where the take stops moving (find_still): frozen, the same picture held for a few frames, or still, almost
nothing moving for a second and a half or more.

Usage: python cuts.py a.mp4 b.mp4 [c.mp4 ...]      scores each cut between neighbours, prints a table
       python cuts.py --json a.mp4 b.mp4
       python cuts.py --scan film.mp4 [--all]     every cut inside one file that needs a look (--all: every cut)
       python cuts.py --scan film.mp4 --at 113,234,355   only the cuts at these frames: where the edit put them.
                                                  A screen recording or an animation changes abruptly inside one
                                                  shot, and a scan without --at reports each of those as a cut
       python cuts.py --inside take.mp4           where a take that should be one shot changes picture or stops moving
       python cuts.py --still film.mp4            where a finished file holds a frozen or still picture
Needs ffmpeg and numpy.
"""
import json
import subprocess
import sys

import numpy as np

W, H, N, FPS = 96, 54, 5, 24
STRAY = 3           # a shot of this many frames or fewer is a stray frame, not a shot
SPAN = 12           # a fade is looked for across this many frames
BLEND = 0.5         # a dissolve's middle frame is a mix of its ends: it strays from their average by less than this part of their difference
POP = 0.12          # a step under this is a blink, a gesture or a ripple, not a change of picture
FROZEN = 0.0005     # a frame that changes less than this from the one before repeats it
FROZEN_RUN = 3      # this many repeated frames in a row are reported as frozen
STILL = 0.003       # a frame that changes less than this from the frame LAG before barely moves
LAG = 6             # still is measured over a quarter of a second: grain and noise do not add up, motion does
STILL_RUN = 36      # this many such frames in a row (1.5 s) are reported as still


def plain(args):
    return args


def window(path, first, count, command=plain):
    """count frames of a file from frame first on, as float RGB arrays of H x W x 3 in 0..1. Fewer if the file ends."""
    first = max(0, int(first))
    raw = subprocess.run(command(["ffmpeg", "-v", "error", "-ss", "%.4f" % (first / float(FPS)), "-i", str(path), "-an",
                                  "-vf", "fps=%d,scale=%d:%d:flags=area" % (FPS, W, H), "-frames:v", str(count),
                                  "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]), capture_output=True, check=True).stdout
    got = np.frombuffer(raw, dtype=np.uint8).reshape(-1, H, W, 3).astype(np.float64) / 255.0
    if not len(got):
        raise ValueError("no picture in %s at frame %d" % (path, first))
    return got


def score_cut(a, a_out, b, b_in, command=plain):
    """Score a cut on a timeline: file a played up to its frame a_out (not included), then file b from its frame b_in."""
    count = min(N, int(a_out))
    m = measure(window(a, a_out - count, count, command), window(b, b_in, N, command))
    return dict(judge(m), **{k: round(v, 4) for k, v in m.items()})


RUN = 9             # frames a camera move is read over: eight steps, a third of a second
RUNS = 3            # this many such runs on each side of a cut, to learn how much the move varies by itself
MOVING = 0.03       # widths a second under which the camera is still
STEADY = 0.04       # widths a second a move may change by at a cut before it reads as a change


def _smooth(y):
    p = np.pad(y, 1, mode="edge")
    return sum(p[i:i + y.shape[0], j:j + y.shape[1]] for i in range(3) for j in range(3)) / 9.0


def _global_flow(a, b):
    """The pan (pixels) and the zoom (per pixel from the centre) that take luma a to luma b.

    Lucas and Kanade's brightness constancy, solved for one motion of the whole frame: every pixel moves by
    (pan_x + zoom * x, pan_y + zoom * y), x and y counted from the centre. Least squares over all pixels, three
    rounds, each weighting down the pixels the last round fitted worst, so a person moving through the frame does
    not drag the camera's move with them.
    """
    a, b = _smooth(a), _smooth(b)
    gy, gx = np.gradient((a + b) / 2)
    h, w = a.shape
    y, x = np.mgrid[0:h, 0:w]
    x, y = x - (w - 1) / 2.0, y - (h - 1) / 2.0
    cols = np.stack([gx.ravel(), gy.ravel(), (gx * x + gy * y).ravel()], axis=1)
    rhs = -(b - a).ravel()
    weight = np.ones(len(rhs))
    found = np.zeros(3)
    for _ in range(3):
        m = cols * weight[:, None]
        found = np.linalg.lstsq(m.T @ cols + 1e-9 * np.eye(3), m.T @ rhs, rcond=None)[0]
        rest = rhs - cols @ found
        scale = np.median(np.abs(rest)) * 1.4826 + 1e-6
        weight = 1.0 / (1.0 + (rest / (2 * scale)) ** 2)
    return found


def camera(pictures):
    """How the camera moves over a run of frames: (pan_x, pan_y, zoom), the median over its steps.

    pan is how far the picture slides, in widths a second: positive x is the picture moving right (the camera
    panning left), positive y the picture moving down (the camera tilting up). zoom is how fast the picture grows,
    in parts a second: 0.1 is a push-in that enlarges it by a tenth each second, negative a pull-back.
    A step of more than a pixel at this size is read at half size, where the same move is small enough to solve.
    """
    rows = []
    for one, two in zip(pictures[:-1], pictures[1:]):
        a, b = luma(one), luma(two)
        small = [y.reshape(H // 2, 2, W // 2, 2).mean(axis=(1, 3)) for y in (a, b)]
        px, py, zoom = _global_flow(*small)
        if np.hypot(px, py) * 2 > 1.0 or abs(zoom) * W / 2 > 1.0:
            px, py = px * 2, py * 2
        else:
            px, py, zoom = _global_flow(a, b)
        rows.append((px / W * FPS, py / W * FPS, zoom * FPS))
    if not rows:
        return (0.0, 0.0, 0.0)
    return tuple(float(v) for v in np.median(np.array(rows), axis=0))


def _as_speed(move):
    """A camera move as one vector in widths a second: a zoom moves the edge of the picture by half its rate."""
    return np.array([move[0], move[1], move[2] / 2.0])


def say_move(move):
    """A camera move in words: its largest part."""
    speed = _as_speed(move)
    if float(np.linalg.norm(speed)) < MOVING:
        return "still"
    part = int(np.argmax(np.abs(speed)))
    if part == 2:
        return "pushing in" if move[2] > 0 else "pulling back"
    if part == 0:
        return "panning left" if move[0] > 0 else "panning right"
    return "tilting up" if move[1] > 0 else "tilting down"


def measure_movement(before, after):
    """The camera's move on each side of a cut. before: the outgoing frames, last one last, up to RUNS runs of them.
    after: the incoming frames. A run is RUN frames; neighbouring runs share one frame."""
    def runs(pictures, tail):
        count = min(RUNS, (len(pictures) - 1) // (RUN - 1))
        found = []
        for n in range(count):
            first = len(pictures) - 1 - (n + 1) * (RUN - 1) if tail else n * (RUN - 1)
            found.append(camera(pictures[first:first + RUN]))
        return found[::-1] if tail else found

    a, b = runs(before, True), runs(after, False)
    if not a or not b:
        return None
    moves = [float(np.linalg.norm(_as_speed(x) - _as_speed(y))) for side in (a, b) for x, y in zip(side[:-1], side[1:])]
    return {"before": a[-1], "after": b[0], "step": float(np.linalg.norm(_as_speed(a[-1]) - _as_speed(b[0]))),
            "usual": float(np.median(moves)) if moves else 0.0}


def judge_movement(m, same_shot=True):
    """kind, score and what is wrong, from the numbers of measure_movement().

    kind   carries on     the move changes by no more than STEADY, or twice what it changes by around the cut
           stops, starts  moving on one side and still on the other
           reverses       moving on both, in opposite directions
           changes        moving on both, the same way, at another speed
    score  0..100, for a cut inside one shot: carries on 90-100, the others lose 4 for every hundredth of a width
           a second past what is allowed. A cut to a new shot has no score: there the move is the editor's choice,
           and the note says what it is.
    """
    if m is None:
        return {"kind": "unknown", "score": None, "notes": ["too few frames to read a move"]}
    before, after = _as_speed(m["before"]), _as_speed(m["after"])
    allowed = max(STEADY, 2 * m["usual"])
    fast = (float(np.linalg.norm(before)) >= MOVING, float(np.linalg.norm(after)) >= MOVING)
    words = (say_move(m["before"]), say_move(m["after"]))
    if m["step"] <= allowed or not any(fast):
        # Still on both sides is no move to carry: what is left is the tremor of a hand or of the grain.
        kind, score, notes = "carries on", 100 - 10 * min(1.0, m["step"] / allowed), []
    else:
        if fast[0] and not fast[1]:
            kind = "stops"
        elif fast[1] and not fast[0]:
            kind = "starts"
        elif float(before @ after) < 0:
            kind = "reverses"
        else:
            kind = "changes"
        score = 90 - 400 * (m["step"] - allowed)
        notes = ["the camera is %s before the cut and %s after it" % words]
    if not same_shot:
        score = None
    return {"kind": kind, "score": None if score is None else round(max(0.0, min(100.0, score))), "notes": notes}


def score_movement(a, a_out, b, b_in, same_shot=True, command=plain):
    """Score how the camera's move carries across a cut: file a up to its frame a_out (not included), then b from b_in."""
    reach = RUNS * (RUN - 1) + 1
    count = min(reach, int(a_out))
    m = measure_movement(window(a, a_out - count, count, command), window(b, b_in, reach, command))
    found = judge_movement(m, same_shot)
    if m is not None:
        found.update({"before": [round(v, 3) for v in m["before"]], "after": [round(v, 3) for v in m["after"]],
                      "step": round(m["step"], 3), "usual": round(m["usual"], 3)})
    return found


PW, PH = 192, 108   # the size the parts of a frame are looked at: twice the size cuts are scored at
ROWS, COLS = 6, 12  # the frame as a grid of parts, each 16 x 18 pixels at that size
LIVELY = 0.003      # a part that changes by this much from frame to frame, beyond the camera's move, is moving
QUIET = 0.001       # a part that changes by less than this is still
SOME = 0.05         # a share of the frame that may change state at a cut before it is worth a word


def window_at(path, first, count, width, height, command=plain):
    """count frames of a file from frame first on, as luma arrays of height x width in 0..1. Fewer if the file ends."""
    first = max(0, int(first))
    raw = subprocess.run(command(["ffmpeg", "-v", "error", "-ss", "%.4f" % (first / float(FPS)), "-i", str(path), "-an",
                                  "-vf", "fps=%d,scale=%d:%d:flags=area,format=gray" % (FPS, width, height), "-frames:v", str(count),
                                  "-f", "rawvideo", "-pix_fmt", "gray", "-"]), capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, height, width).astype(np.float64) / 255.0


def lively(lumas):
    """How much each part of the frame moves over a run of frames, beyond what the camera's own move explains.

    For each step, the camera's pan and zoom are fitted to the whole frame (_global_flow) and the change they
    predict is taken out. What is left is what moves inside the frame: a hand, a wave, rain on a window, a beam of
    light. A ROWS x COLS grid, the median over the run of each part's mean leftover change, in 0..1.
    """
    rows = []
    for a, b in zip(lumas[:-1], lumas[1:]):
        px, py, zoom = _global_flow(a, b)
        sa, sb = _smooth(a), _smooth(b)
        gy, gx = np.gradient((sa + sb) / 2)
        h, w = a.shape
        y, x = np.mgrid[0:h, 0:w]
        x, y = x - (w - 1) / 2.0, y - (h - 1) / 2.0
        left = (sb - sa) + gx * (px + zoom * x) + gy * (py + zoom * y)
        rows.append(np.abs(left).reshape(ROWS, h // ROWS, COLS, w // COLS).mean(axis=(1, 3)))
    return np.median(np.array(rows), axis=0)


def where(mask):
    """Where in the frame the parts of a mask lie, in words."""
    share = float(mask.mean())
    if share >= 0.5:
        return "most of the frame"
    ys, xs = np.nonzero(mask)
    x, y = float(xs.mean()) / (COLS - 1), float(ys.mean()) / (ROWS - 1)
    across = "left" if x < 0.36 else "right" if x > 0.64 else "middle"
    up = "upper " if y < 0.34 else "lower " if y > 0.66 else ""
    return "the %s%s of the frame" % (up, across) if across != "middle" or up else "the middle of the frame"


def measure_parts(before, after):
    """What moves in each part of the frame on each side of a cut. before: the outgoing lumas, last one last, at
    least 2 * RUN - 1 of them when there are that many. after: the incoming ones."""
    def two(lumas, tail):
        if len(lumas) < RUN:
            return None
        near = lumas[-RUN:] if tail else lumas[:RUN]
        far = (lumas[-(2 * RUN - 1):-(RUN - 1)] if tail else lumas[RUN - 1:2 * RUN - 1]) if len(lumas) >= 2 * RUN - 1 else None
        return lively(near), (lively(far) if far is not None else None)

    a, b = two(before, True), two(after, False)
    if a is None or b is None:
        return None

    def changed(one, other):
        return (one >= LIVELY) & (other <= QUIET)

    stops, starts = changed(a[0], b[0]), changed(b[0], a[0])
    # How much of the frame changes state from one run to the next with no cut between them.
    usual = [float((changed(far, near) | changed(near, far)).mean()) for near, far in (a, b) if far is not None]
    return {"stops": stops, "starts": starts, "usual": max(usual) if usual else 0.0,
            "before": float(a[0].mean()), "after": float(b[0].mean())}


def judge_parts(m, same_shot=True):
    """kind, score and what is wrong, from measure_parts().

    kind   carries on   no more of the frame changes state at the cut than SOME, or than does by itself around it
           stops        parts that moved before the cut are still after it
           starts       parts that were still before the cut move after it
           swaps        both
    score  0..100, for a cut inside one shot: a point and a half for every hundredth of the frame past what is
           allowed. A cut to a new shot has no score.
    """
    if m is None:
        return {"kind": "unknown", "score": None, "notes": ["too few frames to tell what moves"]}
    stops, starts = float(m["stops"].mean()), float(m["starts"].mean())
    # A take in the middle of a dissolve changes all over from one run to the next: that excuses half the frame, no more.
    allowed = min(0.5, max(SOME, 2 * m["usual"]))
    notes = []
    if stops > allowed:
        notes.append("what moves in %s before the cut is still after it" % where(m["stops"]))
    if starts > allowed:
        notes.append("%s is still before the cut and moves after it" % where(m["starts"]))
    kind = "carries on" if not notes else "swaps" if len(notes) == 2 else "stops" if stops > allowed else "starts"
    worst = max(stops, starts)
    score = 100 - 10 * min(1.0, worst / allowed) if not notes else 90 - 150 * (worst - allowed)
    if not same_shot:
        score = None
    return {"kind": kind, "score": None if score is None else round(max(0.0, min(100.0, score))), "notes": notes,
            "stops": round(stops, 3), "starts": round(starts, 3), "usual": round(m["usual"], 3)}


def score_parts(a, a_out, b, b_in, same_shot=True, command=plain):
    """Score whether what moves inside the frame carries across a cut: file a up to a_out (not included), then b from b_in."""
    reach = 2 * RUN - 1
    count = min(reach, int(a_out))
    m = measure_parts(window_at(a, a_out - count, count, PW, PH, command), window_at(b, b_in, reach, PW, PH, command))
    return judge_parts(m, same_shot)


SHORT = 8.0         # percent darker or lighter than its pinned frame at which a take has not arrived on it


def judge_arrival(last, pin, cut):
    """Did a take that ends on a pinned frame get there. last: its last frame, pin: the frame it was pinned to (both
    RGB arrays), cut: the judgement of the cut the timeline plays, from the take's last frame but one into the clip
    the pin came from.

    A pinned frame is honoured on the take's very last frame even when the frames before it were heading somewhere
    else, so two things are looked at. The level: a take still fading up or down when it ends lands darker or
    lighter than its pin (a few percent is the decoder; SHORT or more is the picture). And the cut itself: a jump
    there means the frames before the pin did not lead to it.
    """
    a, b = float(luma(last).mean()), float(luma(pin).mean())
    level = 100.0 * (a - b) / max(b, 1e-6)
    notes, score = [], float(cut["score"])
    if abs(level) >= SHORT:
        notes.append("ends %d%% %s than the frame it is pinned to" % (round(abs(level)), "darker" if level < 0 else "lighter"))
        score = min(score, 90 - 3 * (abs(level) - SHORT))
    if cut["kind"] != "continuous":
        notes.append("the picture does not lead into the next clip: " + ("; ".join(cut["notes"]) or cut["kind"]))
    kind = "arrives" if not notes else "short of its pin"
    return {"kind": kind, "score": round(max(0.0, min(100.0, score))), "notes": notes, "level": round(level, 1)}


def score_arrival(a, a_frames, b, b_frame, command=plain):
    """Score how a take of a_frames frames, pinned to end on frame b_frame of file b, arrives there."""
    last = window(a, a_frames - 1, 1, command)[-1]
    pin = window(b, b_frame, 1, command)[0]
    return judge_arrival(last, pin, score_cut(a, a_frames - 1, b, b_frame, command))


def frames(path, tail):
    """The last (tail) or first N frames of a file, as float RGB arrays of H x W x 3 in 0..1."""
    seek = ["-sseof", "-1"] if tail else []
    limit = [] if tail else ["-frames:v", str(N)]
    raw = subprocess.run(["ffmpeg", "-v", "error"] + seek + ["-i", str(path), "-an", "-vf", "scale=%d:%d:flags=area" % (W, H)] + limit
                         + ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    got = np.frombuffer(raw, dtype=np.uint8).reshape(-1, H, W, 3).astype(np.float64) / 255.0
    if not len(got):
        raise ValueError("no picture in %s" % path)
    return got[-N:] if tail else got[:N]


def luma(rgb):
    return rgb @ np.array([0.2126, 0.7152, 0.0722])


def blurred(y):
    return y.reshape(H // 3, 3, W // 3, 3).mean(axis=(1, 3))


def local(a, b):
    """How much two pictures differ where they differ most: the 95th percentile of the difference over 6x6 blocks.
    A subject that pops to another pose changes a small part of the frame a lot, which a mean over the frame hides."""
    return float(np.percentile(np.abs(a - b).reshape(H // 6, 6, W // 6, 6).mean(axis=(1, 3)), 95))


def measure(before, after):
    """The numbers for one cut. before: the outgoing frames, last one last. after: the incoming frames, first one first."""
    a, b = luma(before[-1]), luma(after[0])
    step = local(a, b)
    around = [local(luma(x), luma(y)) for x, y in list(zip(before[:-1], before[1:])) + list(zip(after[:-1], after[1:]))]
    motion = float(np.median(around)) if around else 0.0
    sa, sb = blurred(a), blurred(b)
    da, db = sa - sa.mean(), sb - sb.mean()
    spread = float(np.sqrt((da * da).sum() * (db * db).sum()))
    alike = float((da * db).sum() / spread) if spread > 1e-9 else (1.0 if step < 1e-4 else 0.0)
    cross = np.fft.fft2(a - a.mean()) * np.conj(np.fft.fft2(b - b.mean()))
    peak = np.fft.ifft2(cross / np.maximum(np.abs(cross), 1e-9)).real
    y, x = np.unravel_index(int(peak.argmax()), peak.shape)
    shift = float(np.hypot(min(x, W - x) / W, min(y, H - y) / W))
    colour = float(np.abs(before[-1].mean(axis=(0, 1)) - after[0].mean(axis=(0, 1))).max())
    return {"step": step, "motion": motion, "alike": alike, "shift": shift, "colour": colour}


def judge(m):
    """kind, score and what is wrong, from the numbers of measure()."""
    notes = []
    # A join between a clip and its extension steps by about 0.02 even when nothing moves: the two were decoded apart.
    allowed = 2 * m["motion"] + 0.02
    if m["step"] <= allowed:
        kind, score = "continuous", 100 - 10 * min(1.0, m["step"] / allowed)
        if m["colour"] > 0.02:
            score -= min(20.0, 400 * (m["colour"] - 0.02))
            notes.append("colour changes")
    elif m["alike"] >= 0.5:
        # The worse the more alike the two pictures are, and eased in over the first 0.03 past what is allowed,
        # so a cut just over the line does not fall from 90 to 0.
        worst = 60 * (1 - (m["alike"] - 0.5) / 0.5)
        kind, score = "jump", 90 - min(1.0, (m["step"] - allowed) / 0.03) * (90 - worst)
        notes.append("same shot, the subject pops" if m["shift"] < 0.02 else "same shot, moved %d%% of the width" % round(100 * m["shift"]))
        if m["colour"] > 0.04:
            notes.append("colour changes")
    else:
        kind, score = "new shot", 70 + 30 * (1 - max(m["alike"], 0.0) / 0.5)
    if m["motion"] < 0.001:
        notes.append("still on both sides")
    return {"kind": kind, "score": round(max(0.0, min(100.0, score))), "notes": notes}


def score(a, b):
    """Score the cut from file a to file b."""
    m = measure(frames(a, True), frames(b, False))
    return dict(judge(m), **{k: round(v, 4) for k, v in m.items()})


def whole(path, command=plain):
    """Every frame of a file at W x H, as float RGB in 0..1."""
    raw = subprocess.run(command(["ffmpeg", "-v", "error", "-i", str(path), "-an", "-vf", "fps=%d,scale=%d:%d:flags=area" % (FPS, W, H),
                                  "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]), capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, H, W, 3).astype(np.float32) / 255.0


def find_cuts(pictures):
    """The cuts inside a run of frames: [{"at", kind, score, notes, ...}], at being the first frame after the cut.

    A shot of STRAY frames or fewer between two cuts is one finding, a stray frame, with "frames" saying how many.
    """
    y = [luma(p.astype(np.float64)) for p in pictures]
    steps = [local(a, b) for a, b in zip(y[:-1], y[1:])]
    found = []
    for k, step in enumerate(steps):
        if step <= 0.02:
            continue                                   # under what any cut is allowed: not worth measuring
        before = pictures[max(0, k - N + 1):k + 1].astype(np.float64)
        after = pictures[k + 1:k + 1 + N].astype(np.float64)
        m = measure(before, after)
        verdict = judge(m)
        if verdict["kind"] != "continuous":
            found.append(dict(verdict, at=k + 1, **{name: round(value, 4) for name, value in m.items()}))
    out, k = [], 0
    while k < len(found):
        cut = found[k]
        strong = lambda c: c["kind"] == "new shot" or c["step"] >= POP       # a real change of picture, not a ripple
        if k + 1 < len(found) and found[k + 1]["at"] - cut["at"] <= STRAY and strong(cut) and strong(found[k + 1]):
            count = found[k + 1]["at"] - cut["at"]
            out.append({"at": cut["at"], "kind": "stray frame", "score": 0, "frames": count,
                        "notes": ["%d frame%s of another picture between two cuts" % (count, "" if count == 1 else "s")]})
            k += 2
        else:
            out.append(cut)
            k += 1
    return out


def likeness(a, b):
    """How alike two luma pictures are in layout, -1..1."""
    sa, sb = blurred(a), blurred(b)
    da, db = sa - sa.mean(), sb - sb.mean()
    spread = float(np.sqrt((da * da).sum() * (db * db).sum()))
    return float((da * db).sum() / spread) if spread > 1e-9 else 1.0


def blended(a, middle, b):
    """Whether the middle picture is close to a mix of a and b, as the middle of a dissolve is."""
    spread = float(np.abs(a - b).mean())
    return spread > 1e-6 and float(np.abs(middle - (a + b) / 2).mean()) < BLEND * spread / 2


def find_inside(pictures):
    """Where a run of frames that should be one shot changes picture: [{"at", "kind", "score", "notes"}].

    kind is "cut" (a hard cut, or a stray frame) or "fade" (the picture dims or dissolves into another within SPAN
    frames, with no cut). at is the first frame of the change.
    """
    found = []
    hard = find_cuts(pictures)
    hard = [cut for cut in hard if cut["kind"] != "jump" or cut["step"] >= POP]
    for cut in hard:
        stray = cut["kind"] == "stray frame"
        found.append({"at": cut["at"], "kind": "cut", "score": 0 if stray or cut["kind"] == "new shot" else min(cut["score"], 60),
                      "notes": cut["notes"] if stray else ["the take cuts to another picture" if cut["kind"] == "new shot"
                                                           else "the subject pops inside the take"]})
    y = [luma(p.astype(np.float64)) for p in pictures]
    light = [float(v.mean()) for v in y]
    k = SPAN
    while k < len(y):
        if any(k - SPAN < cut["at"] <= k for cut in hard):
            k += 1
            continue
        dims = light[k] < 0.6 * light[k - SPAN] and light[k - SPAN] > 0.05
        # Another picture through a dissolve passes through mixes of the two. A big move (a dance, a camera move)
        # changes the layout as much, but its middle frame is a picture of its own, not a mix of the ends.
        other = likeness(y[k], y[k - SPAN]) < 0.3 and blended(y[k - SPAN], y[k - SPAN // 2], y[k])
        if dims or other:
            start = k - SPAN
            if dims:                                   # where it starts to dim, not where it was last whole
                start = next(j for j in range(k - SPAN + 1, k + 1) if light[j] < 0.95 * light[k - SPAN]) - 1
            found.append({"at": start, "kind": "fade", "score": 0,
                          "notes": ["the picture fades out" if dims else "the picture dissolves into another one"]})
            k += 3 * SPAN                              # one finding for one fade
        else:
            k += 1
    return sorted(found, key=lambda row: row["at"])


def find_still(pictures):
    """Where a run of frames stops moving: [{"at", "kind", "frames", "score", "notes"}], at being the first frame of
    the stretch and frames how many it holds.

    frozen  FROZEN_RUN or more frames in a row that each change less than FROZEN from the one before: a held or
            stalled picture. After encoding a held frame still changes about 0.0001; the quietest frames of 75
            rendered takes changed 0.0006 or more.
    still   STILL_RUN frames or more that each change less than STILL from the frame LAG before: a take gone dead,
            or a picture with only grain on it. Frame to frame, grain changes as much as a quiet real take, but it
            does not add up over a quarter of a second: a still picture with grain stayed under 0.002 there, and
            no rendered take of the 75 went under 0.003 for more than 3 frames in a row.
    A stretch that is frozen is not also reported as still.
    """
    y = [luma(p.astype(np.float64)) for p in pictures]

    def runs(changes, limit, least, apart):
        """Stretches where changes stay under limit: (first frame, frames). changes[i] compares frame i with frame
        i + apart. With apart 1 the stretch is the frames that repeat the one before; otherwise all it spans."""
        found, start = [], None
        for k, change in enumerate(list(changes) + [1.0]):
            if change < limit and start is None:
                start = k
            elif change >= limit and start is not None:
                first, count = (start + 1, k - start) if apart == 1 else (start, k - start + apart)
                if count >= least:
                    found.append((first, count))
                start = None
        return found

    steps = [local(a, b) for a, b in zip(y[:-1], y[1:])]
    lagged = [local(y[k - LAG], y[k]) for k in range(LAG, len(y))]
    rows = []
    frozen = runs(steps, FROZEN, FROZEN_RUN, 1)
    for at, count in frozen:
        rows.append({"at": at, "kind": "frozen", "frames": count, "score": 10,
                     "notes": ["the picture stops for %d frame%s (%.2f s): a held or stalled frame" % (count, "" if count == 1 else "s", count / float(FPS))]})
    for at, count in runs(lagged, STILL, STILL_RUN, LAG):
        held = sum(max(0, min(at + count, a + c) - max(at, a)) for a, c in frozen)
        if count - held >= STILL_RUN:
            rows.append({"at": at, "kind": "still", "frames": count, "score": 55,
                         "notes": ["almost nothing moves for %.1f s" % (count / float(FPS))]})
    return sorted(rows, key=lambda row: row["at"])


def inside(path, command=plain):
    """Where one take changes picture or stops moving, each with its time in seconds."""
    pictures = whole(path, command)
    rows = sorted(find_inside(pictures) + find_still(pictures), key=lambda row: row["at"])
    return [dict(row, time=round(row["at"] / float(FPS), 3)) for row in rows]


def stills(path, command=plain):
    """Every stretch of one file where the picture stops moving, each with its time in seconds."""
    return [dict(row, time=round(row["at"] / float(FPS), 3)) for row in find_still(whole(path, command))]


def scan(path, command=plain):
    """Every cut inside one file, in order, each with its time in seconds."""
    return [dict(cut, time=round(cut["at"] / float(FPS), 3)) for cut in find_cuts(whole(path, command))]


def main(argv):
    as_json = "--json" in argv
    files = [f for f in argv if not f.startswith("--")]
    if "--inside" in argv or "--still" in argv:
        if len(files) != 1:
            raise SystemExit(__doc__)
        rows = inside(files[0]) if "--inside" in argv else stills(files[0])
        if as_json:
            print(json.dumps(rows, indent=1))
            return
        for r in rows:
            print("%3d  %-6s %d:%05.2f  frame %-6d %s" % (r["score"], r["kind"], int(r["time"]) // 60, r["time"] % 60, r["at"], "; ".join(r["notes"])))
        print(("one shot, nothing found" if "--inside" in argv else "it moves all through") if not rows else "%d found" % len(rows))
        return
    if "--scan" in argv:
        marks = None
        if "--at" in argv:
            marks = [int(n) for n in argv[argv.index("--at") + 1].split(",") if n.strip()]
            files = [f for f in files if f != argv[argv.index("--at") + 1]]
        if len(files) != 1:
            raise SystemExit(__doc__)
        rows = scan(files[0])
        if marks is not None:
            rows = [r for r in rows if any(abs(r["at"] - m) <= STRAY for m in marks)]
        if as_json:
            print(json.dumps(rows, indent=1))
            return
        shown = rows if "--all" in argv else [r for r in rows if r["score"] < 70]
        for r in shown:
            print("%3d  %-11s %d:%05.2f  frame %-6d %s" % (r["score"], r["kind"], int(r["time"]) // 60, r["time"] % 60, r["at"], "; ".join(r["notes"])))
        print("%d cuts, %d need a look" % (len(rows), sum(1 for r in rows if r["score"] < 70)))
        return
    if len(files) < 2:
        raise SystemExit(__doc__)
    rows = [dict(score(a, b), cut="%s > %s" % (a, b)) for a, b in zip(files[:-1], files[1:])]
    if as_json:
        print(json.dumps(rows, indent=1))
        return
    for r in rows:
        print("%3d  %-10s step %.3f  motion %.3f  alike %+.2f  %s  %s" % (r["score"], r["kind"], r["step"], r["motion"], r["alike"], r["cut"], "; ".join(r["notes"])))


if __name__ == "__main__":
    main(sys.argv[1:])

"""Needs numpy. No ffmpeg: the frames are made here, from one picture slid and scaled."""

import unittest

try:
    import numpy as np
    import cuts
except ImportError:
    np = None


class Scene:
    """A textured picture the camera can pan over and zoom into."""

    def __init__(self, seed=0):
        rng = np.random.default_rng(seed)
        big = rng.random((cuts.H * 3, cuts.W * 3))
        for _ in range(3):
            p = np.pad(big, 1, mode="edge")
            big = sum(p[i:i + big.shape[0], j:j + big.shape[1]] for i in range(3) for j in range(3)) / 9.0
        self.big = big

    def frame(self, shift_x=0.0, shift_y=0.0, zoom=1.0):
        yy, xx = np.mgrid[0:cuts.H, 0:cuts.W]
        cx, cy = (cuts.W - 1) / 2.0, (cuts.H - 1) / 2.0
        sx = (xx - cx) / zoom + cx - shift_x + cuts.W
        sy = (yy - cy) / zoom + cy - shift_y + cuts.H
        x0, y0 = np.floor(sx).astype(int), np.floor(sy).astype(int)
        fx, fy = sx - x0, sy - y0
        b = self.big
        y = b[y0, x0] * (1 - fx) * (1 - fy) + b[y0, x0 + 1] * fx * (1 - fy) + b[y0 + 1, x0] * (1 - fx) * fy + b[y0 + 1, x0 + 1] * fx * fy
        return np.repeat(y[:, :, None], 3, axis=2)

    def run(self, count, pan=0.0, tilt=0.0, zoom=0.0, start=(0.0, 0.0, 1.0)):
        """count frames of a steady move: pan and tilt in pixels a frame, zoom as a part a frame."""
        return [self.frame(start[0] + pan * n, start[1] + tilt * n, start[2] * (1 + zoom) ** n) for n in range(count)]


@unittest.skipIf(np is None, "needs numpy")
class CameraTest(unittest.TestCase):
    def setUp(self):
        self.scene = Scene()

    def test_a_still_camera_reads_still(self):
        move = cuts.camera(self.scene.run(9))
        self.assertEqual(cuts.say_move(move), "still")

    def test_a_pan_is_read_in_widths_a_second(self):
        # Half a pixel a frame to the right: 0.5 / 96 * 24 = 0.125 widths a second.
        move = cuts.camera(self.scene.run(9, pan=0.5))
        self.assertAlmostEqual(move[0], 0.125, delta=0.03)
        self.assertEqual(cuts.say_move(move), "panning left")
        self.assertEqual(cuts.say_move(cuts.camera(self.scene.run(9, pan=-0.5))), "panning right")

    def test_a_tilt_is_told_from_a_pan(self):
        self.assertEqual(cuts.say_move(cuts.camera(self.scene.run(9, tilt=0.5))), "tilting up")

    def test_a_push_in_is_read_in_parts_a_second(self):
        move = cuts.camera(self.scene.run(9, zoom=0.01))
        self.assertAlmostEqual(move[2], 0.24, delta=0.06)
        self.assertEqual(cuts.say_move(move), "pushing in")
        self.assertEqual(cuts.say_move(cuts.camera(self.scene.run(9, zoom=-0.01))), "pulling back")

    def test_a_fast_pan_is_still_found(self):
        move = cuts.camera(self.scene.run(9, pan=2.0))
        self.assertAlmostEqual(move[0], 0.5, delta=0.2)

    def test_something_moving_through_the_frame_does_not_move_the_camera(self):
        frames = self.scene.run(9)
        for n, frame in enumerate(frames):
            frame[20:32, 10 + 4 * n:22 + 4 * n] = 1.0
        self.assertEqual(cuts.say_move(cuts.camera(frames)), "still")


@unittest.skipIf(np is None, "needs numpy")
class MovementTest(unittest.TestCase):
    def setUp(self):
        self.scene = Scene(1)

    def judged(self, before, after, same_shot=True):
        return cuts.judge_movement(cuts.measure_movement(before, after), same_shot)

    def test_a_move_that_carries_on_scores_high(self):
        frames = self.scene.run(50, zoom=0.005)
        found = self.judged(frames[:25], frames[25:])
        self.assertEqual((found["kind"], found["notes"]), ("carries on", []))
        self.assertGreaterEqual(found["score"], 90)

    def test_a_push_in_that_stops_dead_at_the_cut(self):
        moving = self.scene.run(25, zoom=0.01)
        still = self.scene.run(25, start=(0.0, 0.0, 1.01 ** 24))
        found = self.judged(moving, still)
        self.assertEqual(found["kind"], "stops")
        self.assertEqual(found["notes"], ["the camera is pushing in before the cut and still after it"])
        self.assertLess(found["score"], 70)

    def test_a_move_that_starts_at_the_cut(self):
        found = self.judged(self.scene.run(25), self.scene.run(25, pan=-0.6))
        self.assertEqual((found["kind"], found["notes"]), ("starts", ["the camera is still before the cut and panning right after it"]))

    def test_a_pan_that_turns_round(self):
        found = self.judged(self.scene.run(25, pan=0.6), self.scene.run(25, pan=-0.6, start=(14.4, 0.0, 1.0)))
        self.assertEqual(found["kind"], "reverses")

    def test_a_handheld_shot_may_change_as_much_at_a_cut_as_it_does_by_itself(self):
        # A camera whose pan already swings from run to run: the same swing at the cut is nothing new.
        def swinging(first):
            frames, x = [], first
            for n in range(25):
                x += 0.45 if (n // 8) % 2 == 0 else -0.05
                frames.append(self.scene.frame(x))
            return frames
        steady = self.judged(self.scene.run(25, pan=0.45), self.scene.run(25, start=(10.8, 0.0, 1.0)))
        moving = self.judged(swinging(0.0), swinging(5.0)[8:] + swinging(5.0)[:8])
        self.assertGreater(moving["score"], steady["score"])

    def test_a_cut_to_a_new_shot_is_described_and_not_scored(self):
        found = self.judged(self.scene.run(25, zoom=0.01), Scene(5).run(25), same_shot=False)
        self.assertEqual((found["kind"], found["score"]), ("stops", None))
        self.assertTrue(found["notes"])

    def test_two_still_sides_carry_on_whatever_the_tremor(self):
        m = {"before": (0.02, 0.0, 0.0), "after": (-0.025, 0.0, 0.0), "step": 0.045, "usual": 0.0}
        found = cuts.judge_movement(m)
        self.assertEqual((found["kind"], found["notes"]), ("carries on", []))
        self.assertGreaterEqual(found["score"], 90)

    def test_too_few_frames_say_so(self):
        found = self.judged(self.scene.run(4), self.scene.run(25))
        self.assertEqual((found["kind"], found["score"]), ("unknown", None))


def textured(seed, height, width):
    rng = np.random.default_rng(seed)
    picture = rng.random((height, width))
    for _ in range(2):
        p = np.pad(picture, 1, mode="edge")
        picture = sum(p[i:i + height, j:j + width] for i in range(3) for j in range(3)) / 9.0
    return picture


@unittest.skipIf(np is None, "needs numpy")
class PartsTest(unittest.TestCase):
    """A still room, with something that can move in one corner of it."""

    def setUp(self):
        self.room = textured(7, cuts.PH, cuts.PW)
        self.thing = textured(8, 36, 400)

    def frames(self, count, moving=None, slide=0.0):
        """count lumas. moving: "left" or "right", the corner where a patch of texture runs along. slide: a pan of
        the whole room, in pixels a frame."""
        found = []
        for n in range(count):
            frame = np.roll(self.room, int(round(slide * n)), axis=1).copy()
            if moving:
                x = 0 if moving == "left" else cuts.PW - 48
                frame[0:36, x:x + 48] = self.thing[:, 3 * n:3 * n + 48]
            found.append(frame)
        return found

    def judged(self, before, after, same_shot=True):
        return cuts.judge_parts(cuts.measure_parts(before, after), same_shot)

    def test_a_still_room_carries_on(self):
        found = self.judged(self.frames(17), self.frames(17))
        self.assertEqual((found["kind"], found["score"], found["notes"]), ("carries on", 100, []))

    def test_something_that_keeps_moving_carries_on(self):
        found = self.judged(self.frames(17, "left"), self.frames(17, "left"))
        self.assertEqual(found["kind"], "carries on")

    def test_something_that_moves_before_the_cut_and_is_still_after_it(self):
        found = self.judged(self.frames(17, "left"), self.frames(17))
        self.assertEqual(found["kind"], "stops")
        self.assertEqual(found["notes"], ["what moves in the upper left of the frame before the cut is still after it"])
        self.assertLess(found["score"], 90)

    def test_something_that_starts_moving_at_the_cut(self):
        found = self.judged(self.frames(17), self.frames(17, "right"))
        self.assertEqual(found["kind"], "starts")
        self.assertEqual(found["notes"], ["the upper right of the frame is still before the cut and moves after it"])

    def test_movement_that_changes_sides_is_both(self):
        found = self.judged(self.frames(17, "left"), self.frames(17, "right"))
        self.assertEqual(found["kind"], "swaps")
        self.assertEqual(len(found["notes"]), 2)

    def test_a_camera_that_pans_is_not_the_room_moving(self):
        # The whole picture slides half a pixel a frame on one side and is still on the other: that is the camera's
        # move, which score_movement reports. Nothing inside the frame started or stopped.
        found = self.judged(self.frames(17, slide=0.5), self.frames(17))
        self.assertEqual(found["kind"], "carries on")

    def test_a_cut_to_a_new_shot_is_described_and_not_scored(self):
        found = self.judged(self.frames(17, "left"), self.frames(17), same_shot=False)
        self.assertEqual((found["kind"], found["score"]), ("stops", None))

    def test_too_few_frames_say_so(self):
        self.assertEqual(self.judged(self.frames(5), self.frames(17))["kind"], "unknown")

    def test_where_names_the_place(self):
        mask = np.zeros((cuts.ROWS, cuts.COLS), dtype=bool)
        mask[4:, 8:] = True
        self.assertEqual(cuts.where(mask), "the lower right of the frame")
        mask[:] = True
        self.assertEqual(cuts.where(mask), "most of the frame")


if __name__ == "__main__":
    unittest.main()

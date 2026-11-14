"""Needs numpy. The parts that read files are tried on real renders, not here."""

import unittest

try:
    import numpy as np
    import joins
except ImportError:
    np = None


def frames(count=39, seed=1):
    rng = np.random.default_rng(seed)
    return rng.random((count, 48, 64))


def take(take_id, total):
    return {"id": take_id, "file": take_id + ".mp4", "subfolder": "h3take_projects/p/clips", "frames": total}


def project():
    """A clip, an extension after it, and a clip the extension is bridged to by a third."""
    side_a = {"clip": "c1", "item": "i1", "file": "t1.mp4", "subfolder": "h3take_projects/p/clips", "first": 0, "last": 124}
    side_b = {"clip": "c2", "item": "i2", "file": "t2.mp4", "subfolder": "h3take_projects/p/clips", "first": 39, "last": 141}
    side_c = {"clip": "c4", "item": "i4", "file": "t4.mp4", "subfolder": "h3take_projects/p/clips", "first": 0, "last": 124}
    return {
        "clips": [
            {"id": "c1", "name": "One", "takes": [take("t1", 124)], "active": "t1"},
            {"id": "c2", "name": "After One", "takes": [take("t2", 141)], "active": "t2",
             "link": {"mode": "extend_after", "hold": 39, "a": side_a}},
            {"id": "c3", "name": "Bridge", "takes": [take("t3", 141)], "active": "t3",
             "link": {"mode": "bridge", "hold": 39, "a": side_b, "b": side_c}},
            {"id": "c4", "name": "Four", "takes": [take("t4", 124)], "active": "t4"},
        ],
        "timeline": [
            {"id": "i1", "clip": "c1", "start": 0, "in": 0, "out": 124},
            {"id": "i2", "clip": "c2", "start": 124, "in": 39, "out": 141},
            {"id": "i3", "clip": "c3", "start": 226, "in": 39, "out": 102},
            {"id": "i4", "clip": "c4", "start": 289, "in": 0, "out": 124},
        ],
    }


@unittest.skipIf(np is None, "needs numpy")
class PictureTest(unittest.TestCase):
    def test_the_same_frames_are_alike_everywhere(self):
        found = joins.picture(frames(), frames())
        self.assertEqual(found["level"], 99.0)
        self.assertEqual(found["edge"], 0)
        self.assertEqual(found["darker"], 0.0)

    def test_frames_spoiled_at_the_sources_end_are_counted_as_its_edge(self):
        ours, theirs = frames(), frames()
        ours[-3:] += 0.2
        found = joins.picture(ours, theirs, tail=True)
        self.assertEqual(found["edge"], 3)
        self.assertLess(found["best"]["now"], found["best"]["psnr"])
        self.assertGreaterEqual(found["best"]["deeper"], 3)

    def test_the_edge_of_a_source_held_from_its_start_is_at_its_first_frames(self):
        ours, theirs = frames(), frames()
        ours[:2] += 0.2
        found = joins.picture(ours, theirs, tail=False)
        self.assertEqual(found["edge"], 2)
        self.assertGreaterEqual(found["best"]["deeper"], 2)

    def test_a_cut_inside_the_held_frames_is_clear_of_the_edge(self):
        ours, theirs = frames(), frames()
        ours[-3:] += 0.2
        at_the_edge = joins.picture(ours, theirs, tail=True)["best"]
        handed_over = joins.picture(ours, theirs, tail=True, over=6)["best"]
        self.assertLess(at_the_edge["now"], 20)
        self.assertEqual(handed_over["now"], 99.0)

    def test_a_darker_copy_is_reported_in_percent(self):
        found = joins.picture(frames(), frames() * 0.975)
        self.assertAlmostEqual(found["darker"], 2.5, places=1)

    def test_too_few_frames_measure_nothing(self):
        self.assertIsNone(joins.picture(frames(2), frames(2)))


@unittest.skipIf(np is None, "needs numpy")
class SoundTest(unittest.TestCase):
    def setUp(self):
        self.wave = np.random.default_rng(2).standard_normal(joins.RATE)

    def test_the_same_wave_has_no_lag(self):
        late, alike = joins.lag_of(self.wave, self.wave)
        self.assertEqual(late, 0.0)
        self.assertAlmostEqual(alike, 1.0, places=6)

    def test_a_late_copy_is_found(self):
        late = np.concatenate([np.zeros(400), self.wave])[:len(self.wave)]
        found, alike = joins.lag_of(self.wave, late)
        self.assertAlmostEqual(found, 400 / joins.RATE, places=6)
        self.assertGreater(alike, 0.99)

    def test_silence_has_nothing_to_line_up(self):
        self.assertEqual(joins.lag_of(np.zeros(joins.RATE), np.zeros(joins.RATE)), (None, None))

    def test_a_wave_that_carries_on_does_not_click(self):
        t = np.arange(joins.RATE) / joins.RATE
        tone = np.sin(2 * np.pi * 220 * t)
        self.assertLess(joins.click(tone[:24000], tone[24000:]), 3)

    def test_a_jump_in_the_wave_clicks(self):
        t = np.arange(joins.RATE) / joins.RATE
        tone = np.sin(2 * np.pi * 220 * t)
        self.assertGreater(joins.click(tone[:24000], tone[24000:] + 0.5), 10)


@unittest.skipIf(np is None, "needs numpy")
class ProjectTest(unittest.TestCase):
    def test_every_side_of_every_linked_clip_is_a_join(self):
        found = joins.project_joins(project())
        self.assertEqual([(j["name"], j["tail"], j["at"]) for j in found],
                         [("After One", True, 124), ("Bridge", True, 226), ("Bridge", False, 289)])

    def test_an_extension_holds_the_sources_last_frames_at_its_own_start(self):
        join = joins.project_joins(project())[0]
        self.assertEqual((join["a"], join["b"]), (("t1.mp4", "h3take_projects/p/clips", 85), ("t2.mp4", "h3take_projects/p/clips", 0)))
        self.assertEqual(join["totals"], (124, 141))

    def test_a_bridge_holds_the_next_clips_first_frames_at_its_own_end(self):
        join = joins.project_joins(project())[2]
        self.assertEqual((join["a"][2], join["b"][2]), (0, 102))

    def test_a_join_with_a_handover_is_found_where_its_source_stops_early(self):
        data = project()
        data["clips"][1]["link"]["handover"] = 6
        data["timeline"][0]["out"] = 118
        data["timeline"][1].update({"start": 118, "in": 33})
        join = joins.project_joins(data)[0]
        self.assertEqual((join["over"], join["at"], join["a"][2], join["b"][2]), (6, 118, 85, 0))
        # Cut at the edge of the held frames, the same link no longer plays as it was made.
        data["timeline"][0]["out"] = 124
        self.assertEqual([j["name"] for j in joins.project_joins(data)], ["Bridge", "Bridge"])

    def test_a_source_trimmed_since_is_left_out(self):
        data = project()
        data["timeline"][0]["out"] = 100
        self.assertEqual([j["name"] for j in joins.project_joins(data)], ["Bridge", "Bridge"])

    def test_a_clip_moved_away_from_its_source_is_left_out(self):
        data = project()
        data["timeline"][1]["start"] = 130
        self.assertEqual([j["at"] for j in joins.project_joins(data)], [289])


if __name__ == "__main__":
    unittest.main()

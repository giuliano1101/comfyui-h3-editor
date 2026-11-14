"""Needs torch and safetensors. No ComfyUI."""

import os
import tempfile
import unittest

try:
    import torch
    import safetensors.torch
    import holds
except ImportError:
    torch = None


class _Nested:
    """Stands in for ComfyUI's NestedTensor: the streams of an AV latent."""
    is_nested = True

    def __init__(self, tensors):
        self.tensors = tensors

    def unbind(self):
        return self.tensors


def take(frames=124, channels=4, height=2, width=3):
    """A latent whose every picture step and sound tick holds its own index, so a slice says where it came from."""
    steps = ((frames - 5) // 17) * 5 + 2
    ticks = int(round(frames / 24.0 * 40))
    video = torch.arange(steps, dtype=torch.float32).view(1, 1, steps, 1, 1).expand(1, channels, steps, height, width)
    audio = torch.arange(ticks, dtype=torch.float32).view(1, 1, 1, ticks).expand(1, 8, 2, ticks)
    return _Nested([video.clone(), audio.clone()])


@unittest.skipIf(torch is None, "needs torch and safetensors")
class GridTest(unittest.TestCase):
    def test_a_part_starts_on_a_multiple_of_17_and_is_a_legal_length(self):
        self.assertTrue(holds.on_grid(0, 39))
        self.assertTrue(holds.on_grid(85, 39))
        self.assertFalse(holds.on_grid(39, 39))
        self.assertFalse(holds.on_grid(17, 40))

    def test_sound_is_whole_steps_every_three_frames(self):
        self.assertTrue(holds.whole_ticks(51, 39))
        self.assertFalse(holds.whole_ticks(85, 39))

    def test_the_end_of_a_whole_take_is_already_on_the_grid(self):
        for frames in (39, 90, 124, 141, 362):
            self.assertEqual(holds.snap_out(frames, 39), frames)

    def test_a_trimmed_end_moves_back_by_less_than_17_frames(self):
        for out in range(39, 200):
            snapped = holds.snap_out(out, 39)
            self.assertTrue(holds.on_grid(snapped - 39, 39))
            self.assertTrue(0 <= out - snapped < 17)

    def test_an_end_stays_where_it_is_when_the_clip_has_no_frames_to_give(self):
        self.assertEqual(holds.snap_out(100, 39, least=60), 100)
        self.assertEqual(holds.snap_out(100, 39, least=51), 90)

    def test_a_trimmed_start_moves_forward_by_less_than_17_frames(self):
        for start in range(0, 100):
            snapped = holds.snap_in(start, 39, 1000)
            self.assertTrue(holds.on_grid(snapped, 39))
            self.assertTrue(0 <= snapped - start < 17)
        self.assertEqual(holds.snap_in(39, 39, 85), 39)

    def test_the_file_is_named_after_the_take(self):
        self.assertEqual(holds.hold_path("a/c1_t1_00001_.mp4"), "a/c1_t1.hold.safetensors")
        self.assertEqual(holds.hold_path("a/footage.mp4"), "a/footage.hold.safetensors")


@unittest.skipIf(torch is None, "needs torch and safetensors")
class FileTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.folder.name, "clips", "c1_t1.hold.safetensors")

    def tearDown(self):
        self.folder.cleanup()

    def test_the_whole_take_is_saved_picture_and_sound(self):
        holds.save(self.path, take(124))
        saved = safetensors.torch.load_file(self.path)
        self.assertEqual(tuple(saved["video"].shape), (1, 4, 37, 2, 3))
        self.assertEqual(tuple(saved["audio"].shape), (1, 8, 2, 207))
        self.assertEqual(saved["video"].dtype, torch.float16)

    def test_the_last_frames_come_back_as_they_were_sampled(self):
        holds.save(self.path, take(124))
        held = holds.part(self.path, 85, 124, 124)
        self.assertEqual(held["samples"][0, 0, :, 0, 0].tolist(), [float(n) for n in range(25, 37)])

    def test_any_cut_on_the_grid_can_be_held(self):
        holds.save(self.path, take(124))
        held = holds.part(self.path, 34, 73, 124)
        self.assertEqual(held["samples"][0, 0, :, 0, 0].tolist(), [float(n) for n in range(10, 22)])
        self.assertEqual(holds.part(self.path, 0, 39, 124)["samples"][0, 0, :, 0, 0].tolist(), [float(n) for n in range(12)])

    def test_a_cut_off_the_grid_is_left_to_the_video_file(self):
        holds.save(self.path, take(124))
        self.assertIsNone(holds.part(self.path, 39, 78, 124))
        self.assertIsNone(holds.part(self.path, 80, 119, 124))

    def test_sound_comes_along_when_the_part_takes_whole_steps(self):
        holds.save(self.path, take(141))
        held = holds.part(self.path, 102, 141, 141)
        self.assertEqual(held["audio"][0, 0, 0].tolist(), [float(n) for n in range(170, 235)])
        self.assertEqual(holds.part(self.path, 51, 90, 141)["audio"][0, 0, 0, 0].item(), 85.0)

    def test_sound_is_left_to_the_file_between_steps(self):
        holds.save(self.path, take(124))
        held = holds.part(self.path, 85, 124, 124)
        self.assertNotIn("audio", held)

    def test_a_file_of_another_take_is_not_used(self):
        holds.save(self.path, take(124))
        self.assertIsNone(holds.part(self.path, 102, 141, 141))

    def test_a_picture_alone_is_saved_without_sound(self):
        holds.save(self.path, take(124).tensors[0])
        self.assertNotIn("audio", holds.part(self.path, 0, 39, 124))

    def test_files_from_before_whole_takes_are_still_read(self):
        video = take(124).tensors[0]
        os.makedirs(os.path.dirname(self.path))
        safetensors.torch.save_file({"head": video[:, :, :12].half().contiguous(), "tail": video[:, :, -12:].half().contiguous()}, self.path)
        self.assertEqual(holds.part(self.path, 85, 124, 124)["samples"][0, 0, 0, 0, 0].item(), 25.0)
        self.assertEqual(holds.part(self.path, 0, 39, 124)["samples"][0, 0, -1, 0, 0].item(), 11.0)
        self.assertIsNone(holds.part(self.path, 34, 73, 124))

    def test_no_file_or_a_broken_one_gives_nothing(self):
        self.assertIsNone(holds.part(self.path, 85, 124, 124))
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "wb") as f:
            f.write(b"not a safetensors file")
        self.assertIsNone(holds.part(self.path, 85, 124, 124))


@unittest.skipIf(torch is None, "needs torch and safetensors")
class HeldTest(unittest.TestCase):
    def setUp(self):
        self.window = {"mode": "span", "first": 0, "ticks": 65, "total": 150, "source_second": 0.0}
        self.like = torch.zeros(1, 8, 2, 150)

    def test_a_picture_of_the_right_steps_and_size_is_held(self):
        held = {"samples": torch.ones(1, 4, 12, 22, 40)}
        self.assertIsNotNone(holds.held_picture(held, 12, 352, 640))
        self.assertIsNone(holds.held_picture(held, 12, 1088, 1920))
        self.assertIsNone(holds.held_picture(held, 7, 352, 640))
        self.assertIsNone(holds.held_picture(None, 12, 352, 640))

    def test_sound_of_the_right_ticks_is_held(self):
        held = {"samples": torch.ones(1, 4, 12, 22, 40), "audio": torch.ones(1, 8, 2, 65)}
        self.assertEqual(tuple(holds.held_sound(held, self.window, self.like).shape), (1, 8, 2, 65))

    def test_sound_is_encoded_when_the_hold_has_none_or_does_not_fit(self):
        self.assertIsNone(holds.held_sound({"samples": torch.ones(1, 4, 12, 22, 40)}, self.window, self.like))
        held = {"audio": torch.ones(1, 8, 2, 60)}
        self.assertIsNone(holds.held_sound(held, self.window, self.like))
        held = {"audio": torch.ones(1, 8, 2, 65)}
        self.assertIsNone(holds.held_sound(held, dict(self.window, mode="full"), self.like))
        self.assertIsNone(holds.held_sound(held, dict(self.window, source_second=0.5), self.like))
        self.assertIsNone(holds.held_sound(held, self.window, torch.zeros(1, 16, 2, 150)))
        self.assertIsNone(holds.held_sound(held, None, self.like))


if __name__ == "__main__":
    unittest.main()

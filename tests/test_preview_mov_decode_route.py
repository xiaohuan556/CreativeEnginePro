import unittest
from types import SimpleNamespace

try:
    from ui.preview_player import PreviewPlayer
except ModuleNotFoundError:
    PreviewPlayer = None


@unittest.skipIf(PreviewPlayer is None, "preview dependencies unavailable")
class PreviewMovDecodeRouteTests(unittest.TestCase):
    def test_normal_phone_mov_does_not_use_alpha_raw_decoder(self):
        clip = SimpleNamespace(source_path="phone_vertical.MOV", has_alpha=False)
        self.assertFalse(PreviewPlayer._clip_uses_alpha_decoder(clip))

    def test_transparent_mov_uses_alpha_raw_decoder(self):
        clip = SimpleNamespace(source_path="overlay.mov", has_alpha=True)
        self.assertTrue(PreviewPlayer._clip_uses_alpha_decoder(clip))

    def test_extension_alone_never_enables_alpha_route(self):
        clip = SimpleNamespace(source_path="ordinary.webm", has_alpha=False)
        self.assertFalse(PreviewPlayer._clip_uses_alpha_decoder(clip))


if __name__ == "__main__":
    unittest.main()

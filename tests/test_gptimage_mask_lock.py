import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from ai.providers.image.seedream import _apply_hard_edit_mask


def _png_bytes(image):
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


class GPTImageMaskLockTests(unittest.TestCase):
    def test_opaque_mask_pixels_are_restored_from_scene_master(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "generated.png"
            Image.new("RGB", (8, 4), (0, 0, 255)).save(output)
            source = Image.new("RGB", (8, 4), (255, 0, 0))
            mask = Image.new("RGBA", (8, 4), (255, 255, 255, 255))
            for x in range(3, 5):
                for y in range(1, 3):
                    mask.putpixel((x, y), (255, 255, 255, 0))

            _apply_hard_edit_mask(
                output, _png_bytes(source), _png_bytes(mask))

            with Image.open(output) as result:
                self.assertEqual((255, 0, 0, 255), result.getpixel((0, 0)))
                self.assertEqual((0, 0, 255, 255), result.getpixel((3, 1)))

    def test_mask_lock_scales_scene_master_to_provider_output(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "generated.png"
            Image.new("RGB", (16, 8), (0, 255, 0)).save(output)
            source = Image.new("RGB", (8, 4), (120, 30, 10))
            mask = Image.new("RGBA", (8, 4), (255, 255, 255, 255))

            _apply_hard_edit_mask(
                output, _png_bytes(source), _png_bytes(mask))

            with Image.open(output) as result:
                self.assertEqual((16, 8), result.size)
                self.assertEqual((120, 30, 10, 255), result.getpixel((15, 7)))


if __name__ == "__main__":
    unittest.main()

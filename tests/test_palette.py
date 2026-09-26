"""Unit tests for PaletteExtractor."""

import io
from PIL import Image
from wledsound.mass.palette import PaletteExtractor


def test_palette_extraction_from_bytes():
    extractor = PaletteExtractor()

    # Create a small synthetic image with dominant red and blue squares
    img = Image.new("RGB", (64, 64), color=(255, 0, 0))
    # Add blue region
    for x in range(32):
        for y in range(64):
            img.putpixel((x, y), (0, 100, 255))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw_bytes = buf.getvalue()

    palette = extractor.extract_from_bytes(raw_bytes, max_colors=5)

    assert len(palette) >= 3
    for r, g, b in palette:
        assert 0 <= r <= 255
        assert 0 <= g <= 255
        assert 0 <= b <= 255

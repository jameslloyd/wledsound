"""Album art downloader and vibrant color palette extractor."""

import io
import math
import aiohttp
import logging
from typing import List, Tuple, Optional
from PIL import Image

logger = logging.getLogger(__name__)


def rgb_to_hsl(r: int, g: int, b: int) -> Tuple[float, float, float]:
    """Converts RGB (0-255) to HSL (H: 0-360, S: 0-1, L: 0-1)."""
    rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
    cmax = max(rf, gf, bf)
    cmin = min(rf, gf, bf)
    delta = cmax - cmin

    # Lightness
    l = (cmax + cmin) / 2.0

    if delta == 0:
        h = 0.0
        s = 0.0
    else:
        # Saturation
        s = delta / (1.0 - abs(2.0 * l - 1.0)) if (1.0 - abs(2.0 * l - 1.0)) > 0 else 0.0
        # Hue
        if cmax == rf:
            h = (60.0 * (((gf - bf) / delta) % 6))
        elif cmax == gf:
            h = (60.0 * (((bf - rf) / delta) + 2))
        else:
            h = (60.0 * (((rf - gf) / delta) + 4))

    return (h % 360.0, s, l)


class PaletteExtractor:
    """Extracts harmonious, vibrant color palettes from album artwork."""

    DEFAULT_PALETTE = [
        (255, 120, 0),   # Vibrant Amber
        (255, 40, 100),  # Neon Rose
        (80, 200, 255),  # Electric Cyan
        (160, 50, 255),  # Deep Violet
        (20, 20, 30)     # Dark Base
    ]

    def __init__(self, session: Optional[aiohttp.ClientSession] = None):
        self._session = session

    async def extract_from_url(self, image_url: str) -> List[Tuple[int, int, int]]:
        """Downloads image from URL and extracts color palette."""
        if not image_url:
            return self.DEFAULT_PALETTE

        try:
            close_session = False
            session = self._session
            if session is None or session.closed:
                session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5.0))
                close_session = True

            async with session.get(image_url) as resp:
                if resp.status == 200:
                    image_bytes = await resp.read()
                    palette = self.extract_from_bytes(image_bytes)
                    if close_session:
                        await session.close()
                    return palette
                else:
                    logger.warning(f"Failed to fetch album art ({resp.status}) from {image_url}")

            if close_session:
                await session.close()
        except Exception as e:
            logger.warning(f"Error downloading album art from {image_url}: {e}")

        return self.DEFAULT_PALETTE

    def extract_from_bytes(self, image_bytes: bytes, max_colors: int = 5) -> List[Tuple[int, int, int]]:
        """Extracts dominant colors from raw image bytes using Pillow quantization."""
        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                # Convert to RGB and resize to small thumbnail for fast quantization
                img = img.convert("RGB")
                img.thumbnail((80, 80), Image.Resampling.BOX)

                # Quantize down to 16 dominant colors
                quantized = img.quantize(colors=16, method=Image.Quantize.MEDIANCUT)
                palette_data = quantized.getpalette()[:48]  # 16 * 3 = 48 bytes
                color_counts = quantized.getcolors()

                if not color_counts:
                    return self.DEFAULT_PALETTE

                # Pair colors with their pixel counts
                colors_with_counts = []
                for count, index in color_counts:
                    idx = index * 3
                    r, g, b = palette_data[idx], palette_data[idx + 1], palette_data[idx + 2]
                    colors_with_counts.append(((r, g, b), count))

                # Score colors based on vibrancy (saturation * non-extreme lightness * frequency)
                scored_colors = []
                for (r, g, b), count in colors_with_counts:
                    h, s, l = rgb_to_hsl(r, g, b)
                    # Penalize near-black and near-white
                    lightness_penalty = 1.0 - abs(l - 0.5) * 1.5
                    lightness_penalty = max(0.1, lightness_penalty)
                    # Vibrant score
                    score = (s * 1.5 + lightness_penalty) * math.log(count + 1)
                    scored_colors.append(((r, g, b), score, s, l))

                # Sort by vibrancy score descending
                scored_colors.sort(key=lambda x: x[1], reverse=True)

                # Select distinct colors
                selected: List[Tuple[int, int, int]] = []
                for (color, _, s, l) in scored_colors:
                    # Avoid duplicates or very similar colors
                    is_distinct = True
                    for sel in selected:
                        dist = math.sqrt(
                            (color[0] - sel[0]) ** 2 +
                            (color[1] - sel[1]) ** 2 +
                            (color[2] - sel[2]) ** 2
                        )
                        if dist < 45.0:
                            is_distinct = False
                            break
                    if is_distinct:
                        selected.append(color)
                    if len(selected) >= max_colors:
                        break

                while len(selected) < max_colors:
                    selected.append(self.DEFAULT_PALETTE[len(selected) % len(self.DEFAULT_PALETTE)])

                return selected
        except Exception as e:
            logger.error(f"Failed to extract color palette: {e}")
            return self.DEFAULT_PALETTE

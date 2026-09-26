"""Real-time audio visualizer effects engine for WLED DDP streaming."""

import math
from typing import List, Tuple, Dict, Any, Optional
from ..audio.types import AudioFeatures, TrackInfo
from ..config import WLEDDeviceConfig, WLEDSegmentConfig


AVAILABLE_EFFECTS = [
    {"id": "album_pulse", "name": "Album Pulse", "description": "Pulsing ambient glow with outward beat ripple"},
    {"id": "geq_spectrum", "name": "16-Band GEQ", "description": "16-band graphic equalizer with floating peak hold"},
    {"id": "energy_wave", "name": "Energy Wave", "description": "Dynamic fluid wave shifting with audio energy"},
    {"id": "vu_meter", "name": "VU Meter", "description": "Classic responsive volume meter with color ramp"},
    {"id": "beat_flash", "name": "Beat Flash", "description": "Deep ambient baseline with explosive beat flashes"},
    {"id": "solid", "name": "Solid Ambient", "description": "Smooth breathing ambient light from palette"},
    {"id": "off", "name": "Off (Dark)", "description": "Keeps segment turned completely off/black"}
]


BUILTIN_PALETTES: Dict[str, Dict[str, Any]] = {
    "album_art": {
        "name": "Album Cover (Auto)",
        "colors": None
    },
    "cyberpunk": {
        "name": "Cyberpunk Neon",
        "colors": [(255, 0, 128), (0, 240, 255), (130, 0, 255), (255, 230, 0), (20, 0, 60)]
    },
    "sunset": {
        "name": "Sunset Fire",
        "colors": [(255, 30, 60), (255, 110, 10), (255, 195, 30), (130, 20, 120), (40, 5, 60)]
    },
    "vaporwave": {
        "name": "Vaporwave Retro",
        "colors": [(255, 110, 180), (70, 230, 230), (160, 90, 255), (255, 200, 240), (35, 15, 80)]
    },
    "aurora": {
        "name": "Aurora Borealis",
        "colors": [(0, 255, 160), (0, 200, 240), (30, 100, 255), (180, 50, 255), (5, 20, 60)]
    },
    "magma": {
        "name": "Molten Magma",
        "colors": [(255, 40, 0), (255, 140, 0), (255, 215, 0), (160, 10, 30), (40, 5, 5)]
    },
    "forest": {
        "name": "Emerald Forest",
        "colors": [(0, 230, 110), (80, 255, 60), (0, 160, 140), (200, 210, 40), (10, 40, 25)]
    },
    "glacial": {
        "name": "Glacial Frost",
        "colors": [(240, 250, 255), (130, 220, 255), (40, 150, 255), (15, 70, 200), (5, 15, 50)]
    },
    "rainbow": {
        "name": "Rainbow Prism",
        "colors": [(255, 0, 0), (255, 140, 0), (255, 230, 0), (0, 230, 80), (0, 180, 255), (130, 0, 255)]
    },
    "candle": {
        "name": "Warm Candlelight",
        "colors": [(255, 130, 30), (255, 185, 70), (220, 80, 20), (255, 220, 140), (60, 20, 10)]
    }
}


def get_palette_definitions() -> List[Dict[str, Any]]:
    """Returns list of all available palette definitions for API and Web UI."""
    result = []
    for p_id, p_info in BUILTIN_PALETTES.items():
        colors = p_info["colors"] or [(255, 120, 0), (255, 40, 100), (0, 229, 255)]
        hex_colors = [f"#{r:02x}{g:02x}{b:02x}" for r, g, b in colors]
        result.append({
            "id": p_id,
            "name": p_info["name"],
            "hex_colors": hex_colors,
            "is_dynamic": (p_info["colors"] is None)
        })
    return result


def interpolate_color(c1: Tuple[int, int, int], c2: Tuple[int, int, int], factor: float) -> Tuple[int, int, int]:
    """Linearly interpolates between two RGB colors (factor 0.0 to 1.0)."""
    factor = max(0.0, min(1.0, factor))
    r = int(c1[0] + (c2[0] - c1[0]) * factor)
    g = int(c1[1] + (c2[1] - c1[1]) * factor)
    b = int(c1[2] + (c2[2] - c1[2]) * factor)
    return (r, g, b)


def scale_color(color: Tuple[int, int, int], brightness: float) -> Tuple[int, int, int]:
    """Scales color brightness (0.0 to 1.0)."""
    brightness = max(0.0, min(1.0, brightness))
    return (
        int(color[0] * brightness),
        int(color[1] * brightness),
        int(color[2] * brightness)
    )


class SegmentAnimationState:
    """Maintains independent animation physics and history for a single LED segment."""

    def __init__(self, length: int):
        self.length = max(1, length)
        self.pulse_decay = 0.0
        self.peaks_history = [0.0] * self.length
        self.wave_phase = 0.0

    def resize(self, length: int):
        if length != self.length:
            self.length = max(1, length)
            self.peaks_history = [0.0] * self.length


class VisualizerEngine:
    """Renders real-time audio features into LED strip RGB pixel frames per segment/device."""

    def __init__(self, led_count: int = 60, effect_name: str = "album_pulse", palette_name: str = "album_art"):
        self.led_count = max(1, led_count)
        self.effect_name = effect_name.lower()
        self.palette_name = palette_name.lower()
        self._segment_states: Dict[str, SegmentAnimationState] = {}

    def get_segment_state(self, key: str, length: int) -> SegmentAnimationState:
        """Retrieves or creates state for a given segment key."""
        if key not in self._segment_states:
            self._segment_states[key] = SegmentAnimationState(length)
        else:
            self._segment_states[key].resize(length)
        return self._segment_states[key]

    def set_effect(self, effect_name: str) -> None:
        """Switches the default visualizer effect."""
        self.effect_name = effect_name.lower()

    def set_palette(self, palette_name: str) -> None:
        """Sets the active master color palette name."""
        self.palette_name = palette_name.lower()

    def set_led_count(self, count: int) -> None:
        """Updates default target LED count."""
        self.led_count = max(1, count)

    def get_palette(self, track_palette: Optional[List[Tuple[int, int, int]]] = None) -> List[Tuple[int, int, int]]:
        """Returns the resolved color palette (preset or dynamic album art)."""
        return self.get_palette_by_name(self.palette_name, track_palette)

    def get_palette_by_name(
        self, name: str, track_palette: Optional[List[Tuple[int, int, int]]] = None
    ) -> List[Tuple[int, int, int]]:
        """Resolves palette colors for a specific palette name."""
        p_name = (name or self.palette_name).lower()
        preset = BUILTIN_PALETTES.get(p_name)
        if preset and preset["colors"]:
            return preset["colors"]
        if track_palette:
            return track_palette
        return BUILTIN_PALETTES["cyberpunk"]["colors"]

    def render_effect(
        self,
        effect_name: str,
        count: int,
        features: AudioFeatures,
        palette: List[Tuple[int, int, int]],
        state_key: str = "default"
    ) -> List[Tuple[int, int, int]]:
        """Renders an effect for a specific segment length with its own animation state."""
        count = max(1, count)
        state = self.get_segment_state(state_key, count)
        eff = effect_name.lower() if effect_name else self.effect_name

        if eff == "album_pulse":
            return self._render_album_pulse(features, palette, count, state)
        elif eff == "geq_spectrum":
            return self._render_geq_spectrum(features, palette, count, state)
        elif eff == "energy_wave":
            return self._render_energy_wave(features, palette, count, state)
        elif eff == "vu_meter":
            return self._render_vu_meter(features, palette, count, state)
        elif eff == "beat_flash":
            return self._render_beat_flash(features, palette, count, state)
        elif eff == "solid":
            return self._render_solid(features, palette, count, state)
        elif eff == "off":
            return [(0, 0, 0)] * count
        else:
            return self._render_album_pulse(features, palette, count, state)

    def render_device(
        self,
        device: WLEDDeviceConfig,
        features: AudioFeatures,
        track: TrackInfo,
        global_palette: Optional[List[Tuple[int, int, int]]] = None
    ) -> List[Tuple[int, int, int]]:
        """Renders an entire device frame across all defined segments."""
        total_leds = max(1, device.led_count)
        palette = global_palette or self.get_palette(track.palette)

        if not device.segments:
            return self.render_effect(
                self.effect_name, total_leds, features, palette, state_key=f"{device.ip}_master"
            )

        frame = [(0, 0, 0)] * total_leds
        for seg in device.segments:
            start = max(0, min(total_leds, seg.start))
            stop = max(0, min(total_leds, seg.stop))
            seg_len = stop - start
            if seg_len <= 0:
                continue

            # Resolve palette (segment override or master palette)
            if seg.palette and seg.palette != "inherit":
                seg_palette = self.get_palette_by_name(seg.palette, track.palette)
            else:
                seg_palette = palette

            state_key = f"{device.ip}_{seg.id}"
            effect_name = seg.effect or self.effect_name
            seg_pixels = self.render_effect(effect_name, seg_len, features, seg_palette, state_key)

            # Apply segment modifiers: mirror, reverse, brightness
            if seg.mirror and seg_len > 1:
                half = seg_len // 2
                for i in range(half):
                    seg_pixels[seg_len - 1 - i] = seg_pixels[i]

            if seg.reverse:
                seg_pixels = seg_pixels[::-1]

            if seg.brightness < 0.99:
                bri = max(0.0, min(1.0, seg.brightness))
                seg_pixels = [scale_color(p, bri) for p in seg_pixels]

            frame[start:stop] = seg_pixels

        return frame

    def render(self, features: AudioFeatures, track: TrackInfo) -> List[Tuple[int, int, int]]:
        """Renders one frame of RGB pixels using the default effect and strip count."""
        palette = self.get_palette(track.palette)
        return self.render_effect(self.effect_name, self.led_count, features, palette, state_key="global")

    def _render_album_pulse(
        self, features: AudioFeatures, palette: List[Tuple[int, int, int]], count: int, state: SegmentAnimationState
    ) -> List[Tuple[int, int, int]]:
        """Smooth ambient glow pulsating on bass hits with outward ripple."""
        c_primary = palette[0]
        c_accent = palette[1] if len(palette) > 1 else (255, 255, 255)

        # Trigger pulse on beat
        if features.sample_peak:
            state.pulse_decay = 1.0
        else:
            state.pulse_decay = max(0.0, state.pulse_decay * 0.88)

        norm_smth = features.sample_smth / 255.0
        center = (count - 1) / 2.0
        max_dist = max(1.0, center)

        pixels = []
        for i in range(count):
            dist = abs(i - center) / max_dist
            ripple = max(0.0, 1.0 - abs(dist - (1.0 - state.pulse_decay) * 1.2))

            base_light = scale_color(c_primary, 0.15 + (norm_smth * 0.4))
            pulse_light = scale_color(c_accent, state.pulse_decay * ripple)

            r = min(255, base_light[0] + pulse_light[0])
            g = min(255, base_light[1] + pulse_light[1])
            b = min(255, base_light[2] + pulse_light[2])
            pixels.append((r, g, b))

        return pixels

    def _render_geq_spectrum(
        self, features: AudioFeatures, palette: List[Tuple[int, int, int]], count: int, state: SegmentAnimationState
    ) -> List[Tuple[int, int, int]]:
        """16-Band Graphic Equalizer mapped across the segment with floating peak dots."""
        pixels = []
        num_bands = 16
        bands = features.fft_result

        for i in range(count):
            band_idx = int((i / count) * num_bands)
            band_val = bands[band_idx] / 255.0

            color_progress = i / max(1, count - 1)
            p_idx = color_progress * (len(palette) - 1)
            idx0 = int(p_idx)
            idx1 = min(len(palette) - 1, idx0 + 1)
            color = interpolate_color(palette[idx0], palette[idx1], p_idx - idx0)

            # Decay floating peak
            if band_val > state.peaks_history[i]:
                state.peaks_history[i] = band_val
            else:
                state.peaks_history[i] = max(0.0, state.peaks_history[i] - 0.02)

            is_peak = (band_val >= 0.05 and abs(band_val - state.peaks_history[i]) < 0.05)
            brightness = band_val if not is_peak else 1.0
            brightness = max(0.05, brightness)

            pixels.append(scale_color(color, brightness))

        return pixels

    def _render_energy_wave(
        self, features: AudioFeatures, palette: List[Tuple[int, int, int]], count: int, state: SegmentAnimationState
    ) -> List[Tuple[int, int, int]]:
        """Center-out fluid wave that shifts speed and color with music intensity."""
        speed = 0.05 + (features.sample_smth / 255.0) * 0.2
        state.wave_phase = (state.wave_phase + speed) % (2.0 * math.pi)

        center = (count - 1) / 2.0
        max_dist = max(1.0, center)
        pixels = []

        c1 = palette[0]
        c2 = palette[1] if len(palette) > 1 else (255, 255, 255)
        c3 = palette[2] if len(palette) > 2 else palette[0]

        for i in range(count):
            dist = abs(i - center) / max_dist
            sine_val = (math.sin(dist * 6.0 - state.wave_phase) + 1.0) * 0.5
            intensity = (features.sample_raw / 255.0) * sine_val

            if dist < 0.5:
                color = interpolate_color(c1, c2, dist * 2.0)
            else:
                color = interpolate_color(c2, c3, (dist - 0.5) * 2.0)

            pixels.append(scale_color(color, max(0.08, intensity)))

        return pixels

    def _render_vu_meter(
        self, features: AudioFeatures, palette: List[Tuple[int, int, int]], count: int, state: SegmentAnimationState
    ) -> List[Tuple[int, int, int]]:
        """Stereo or mono VU volume meter with peak hold."""
        level = min(1.0, features.sample_raw / 220.0)
        active_leds = int(level * count)

        pixels = []
        for i in range(count):
            fraction = i / max(1, count - 1)
            if fraction < 0.65:
                color = palette[0]
            elif fraction < 0.85:
                color = palette[1] if len(palette) > 1 else (255, 180, 0)
            else:
                color = (255, 30, 30)

            if i < active_leds:
                pixels.append(color)
            else:
                pixels.append(scale_color(color, 0.05))

        return pixels

    def _render_beat_flash(
        self, features: AudioFeatures, palette: List[Tuple[int, int, int]], count: int, state: SegmentAnimationState
    ) -> List[Tuple[int, int, int]]:
        """Subtle ambient baseline with sudden flash on beat drops."""
        if features.sample_peak:
            state.pulse_decay = 1.0
        else:
            state.pulse_decay = max(0.0, state.pulse_decay * 0.82)

        c_base = palette[-1] if len(palette) > 1 else (20, 20, 40)
        c_flash = palette[1] if len(palette) > 1 else (255, 255, 255)

        base_color = scale_color(c_base, 0.2 + (features.sample_smth / 255.0) * 0.3)
        flash_color = scale_color(c_flash, state.pulse_decay)

        r = min(255, base_color[0] + flash_color[0])
        g = min(255, base_color[1] + flash_color[1])
        b = min(255, base_color[2] + flash_color[2])

        return [(r, g, b)] * count

    def _render_solid(
        self, features: AudioFeatures, palette: List[Tuple[int, int, int]], count: int, state: SegmentAnimationState
    ) -> List[Tuple[int, int, int]]:
        """Solid ambient wash with gentle audio breathing."""
        norm_smth = features.sample_smth / 255.0
        bri = 0.25 + (norm_smth * 0.45)
        col = scale_color(palette[0], bri)
        return [col] * count

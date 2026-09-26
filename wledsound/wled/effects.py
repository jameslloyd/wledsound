"""Real-time audio visualizer effects engine for WLED DDP streaming."""

import math
from typing import List, Tuple
from ..audio.types import AudioFeatures, TrackInfo


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


class VisualizerEngine:
    """Renders real-time audio features and album palette into LED strip RGB pixel frames."""

    def __init__(self, led_count: int = 60, effect_name: str = "album_pulse"):
        self.led_count = max(1, led_count)
        self.effect_name = effect_name

        # State tracking for smooth animations
        self._pulse_decay = 0.0
        self._peaks_history = [0.0] * self.led_count
        self._wave_phase = 0.0
        self._center_wave_history = [0.0] * self.led_count

    def set_effect(self, effect_name: str) -> None:
        """Switches the active rendering effect."""
        self.effect_name = effect_name.lower()

    def set_led_count(self, count: int) -> None:
        """Updates the target LED count."""
        self.led_count = max(1, count)
        self._peaks_history = [0.0] * self.led_count
        self._center_wave_history = [0.0] * self.led_count

    def render(self, features: AudioFeatures, track: TrackInfo) -> List[Tuple[int, int, int]]:
        """Renders one frame of RGB pixels."""
        palette = track.palette
        if not palette:
            palette = [(255, 100, 20), (50, 180, 255), (200, 30, 255)]

        if self.effect_name == "album_pulse":
            return self._render_album_pulse(features, palette)
        elif self.effect_name == "geq_spectrum":
            return self._render_geq_spectrum(features, palette)
        elif self.effect_name == "energy_wave":
            return self._render_energy_wave(features, palette)
        elif self.effect_name == "vu_meter":
            return self._render_vu_meter(features, palette)
        elif self.effect_name == "beat_flash":
            return self._render_beat_flash(features, palette)
        else:
            return self._render_album_pulse(features, palette)

    def _render_album_pulse(
        self, features: AudioFeatures, palette: List[Tuple[int, int, int]]
    ) -> List[Tuple[int, int, int]]:
        """Smooth ambient glow pulsating on bass hits with outward ripple."""
        c_primary = palette[0]
        c_accent = palette[1] if len(palette) > 1 else (255, 255, 255)
        c_base = palette[-1] if len(palette) > 2 else (10, 10, 20)

        # Trigger pulse on beat
        if features.sample_peak:
            self._pulse_decay = 1.0
        else:
            self._pulse_decay = max(0.0, self._pulse_decay * 0.88)

        norm_smth = features.sample_smth / 255.0
        center = (self.led_count - 1) / 2.0
        max_dist = max(1.0, center)

        pixels = []
        for i in range(self.led_count):
            dist = abs(i - center) / max_dist
            # Pulse ripples outward from center
            ripple = max(0.0, 1.0 - abs(dist - (1.0 - self._pulse_decay) * 1.2))

            # Blend between ambient glow and dynamic beat flash
            base_light = scale_color(c_primary, 0.15 + (norm_smth * 0.4))
            pulse_light = scale_color(c_accent, self._pulse_decay * ripple)

            r = min(255, base_light[0] + pulse_light[0])
            g = min(255, base_light[1] + pulse_light[1])
            b = min(255, base_light[2] + pulse_light[2])
            pixels.append((r, g, b))

        return pixels

    def _render_geq_spectrum(
        self, features: AudioFeatures, palette: List[Tuple[int, int, int]]
    ) -> List[Tuple[int, int, int]]:
        """16-Band Graphic Equalizer mapped across the strip with floating peak dots."""
        pixels = []
        num_bands = 16
        bands = features.fft_result

        # Calculate pixels per band
        for i in range(self.led_count):
            band_idx = int((i / self.led_count) * num_bands)
            band_val = bands[band_idx] / 255.0

            # Color along the palette gradient
            color_progress = i / max(1, self.led_count - 1)
            p_idx = color_progress * (len(palette) - 1)
            idx0 = int(p_idx)
            idx1 = min(len(palette) - 1, idx0 + 1)
            color = interpolate_color(palette[idx0], palette[idx1], p_idx - idx0)

            # Decay floating peak
            if band_val > self._peaks_history[i]:
                self._peaks_history[i] = band_val
            else:
                self._peaks_history[i] = max(0.0, self._peaks_history[i] - 0.02)

            # Peak dot brightness
            is_peak = (band_val >= 0.05 and abs(band_val - self._peaks_history[i]) < 0.05)
            brightness = band_val if not is_peak else 1.0
            brightness = max(0.05, brightness)

            pixels.append(scale_color(color, brightness))

        return pixels

    def _render_energy_wave(
        self, features: AudioFeatures, palette: List[Tuple[int, int, int]]
    ) -> List[Tuple[int, int, int]]:
        """Center-out fluid wave that shifts speed and color with music intensity."""
        speed = 0.05 + (features.sample_smth / 255.0) * 0.2
        self._wave_phase = (self._wave_phase + speed) % (2.0 * math.pi)

        center = (self.led_count - 1) / 2.0
        max_dist = max(1.0, center)
        pixels = []

        c1 = palette[0]
        c2 = palette[1] if len(palette) > 1 else (255, 255, 255)
        c3 = palette[2] if len(palette) > 2 else palette[0]

        for i in range(self.led_count):
            dist = abs(i - center) / max_dist
            sine_val = (math.sin(dist * 6.0 - self._wave_phase) + 1.0) * 0.5
            intensity = (features.sample_raw / 255.0) * sine_val

            # Gradient blend
            if dist < 0.5:
                color = interpolate_color(c1, c2, dist * 2.0)
            else:
                color = interpolate_color(c2, c3, (dist - 0.5) * 2.0)

            pixels.append(scale_color(color, max(0.08, intensity)))

        return pixels

    def _render_vu_meter(
        self, features: AudioFeatures, palette: List[Tuple[int, int, int]]
    ) -> List[Tuple[int, int, int]]:
        """Stereo or mono VU volume meter with peak hold."""
        level = min(1.0, features.sample_raw / 220.0)
        active_leds = int(level * self.led_count)

        pixels = []
        for i in range(self.led_count):
            fraction = i / max(1, self.led_count - 1)
            # Classic VU color ramp (green/blue -> amber -> hot red/white)
            if fraction < 0.65:
                color = palette[0]
            elif fraction < 0.85:
                color = palette[1] if len(palette) > 1 else (255, 180, 0)
            else:
                color = (255, 30, 30)

            if i < active_leds:
                pixels.append(color)
            else:
                # Dim background
                pixels.append(scale_color(color, 0.05))

        return pixels

    def _render_beat_flash(
        self, features: AudioFeatures, palette: List[Tuple[int, int, int]]
    ) -> List[Tuple[int, int, int]]:
        """Subtle ambient baseline with sudden full-strip flash on beat drops."""
        if features.sample_peak:
            self._pulse_decay = 1.0
        else:
            self._pulse_decay = max(0.0, self._pulse_decay * 0.82)

        c_base = palette[-1] if len(palette) > 1 else (20, 20, 40)
        c_flash = palette[1] if len(palette) > 1 else (255, 255, 255)

        base_color = scale_color(c_base, 0.2 + (features.sample_smth / 255.0) * 0.3)
        flash_color = scale_color(c_flash, self._pulse_decay)

        r = min(255, base_color[0] + flash_color[0])
        g = min(255, base_color[1] + flash_color[1])
        b = min(255, base_color[2] + flash_color[2])

        return [(r, g, b)] * self.led_count

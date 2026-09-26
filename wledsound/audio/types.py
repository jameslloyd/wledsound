"""Audio feature and data types for wledsound."""

from dataclasses import dataclass, field
from typing import List, Tuple, Optional


@dataclass
class AudioFeatures:
    """Analyzed audio features ready for WLED AudioSync and DDP renderers."""
    # Raw sample amplitude / energy (0.0 - 255.0 or normalized)
    sample_raw: float = 0.0
    # Smoothed / AGC-adjusted sample value (0.0 - 255.0)
    sample_smth: float = 0.0
    # Peak / beat onset detected flag (0 = no peak, 1 = peak)
    sample_peak: int = 0
    # 16-band GEQ spectrum values (0 - 255 each)
    fft_result: List[int] = field(default_factory=lambda: [0] * 16)
    # Magnitude of the strongest frequency peak
    fft_magnitude: float = 0.0
    # Frequency in Hz of the dominant peak
    fft_major_peak: float = 0.0
    # Overall RMS energy level (0.0 - 1.0)
    rms_energy: float = 0.0
    # Sub-sampled waveform for real-time web visualization
    waveform_preview: List[float] = field(default_factory=lambda: [0.0] * 32)


@dataclass
class TrackInfo:
    """Metadata for the currently playing track in Music Assistant."""
    title: str = "Unknown Title"
    artist: str = "Unknown Artist"
    album: str = "Unknown Album"
    image_url: Optional[str] = None
    state: str = "idle"  # "playing", "paused", "idle"
    duration: float = 0.0
    elapsed_time: float = 0.0
    # Extracted dominant color palette [(r, g, b), ...]
    palette: List[Tuple[int, int, int]] = field(
        default_factory=lambda: [
            (255, 120, 0),   # Warm Amber
            (255, 40, 100),  # Neon Rose
            (80, 200, 255),  # Electric Cyan
            (160, 50, 255),  # Deep Violet
            (20, 20, 30)     # Dark Base
        ]
    )

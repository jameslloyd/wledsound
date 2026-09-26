"""Digital Signal Processing (DSP) engine for real-time audio analysis.

Transforms raw PCM audio into 16-band GEQ spectrum data, peak detection,
and smoothed sample levels compliant with WLED AudioReactive V2 sync.
"""

import math
import numpy as np
from typing import List, Tuple
from .types import AudioFeatures


class AudioDSP:
    """Real-time audio analyzer for WLED music reactivity."""

    # 16 standard frequency band center frequencies (Hz) for WLED GEQ
    BAND_CENTERS = [
        60, 100, 160, 250, 400, 630, 1000, 1600,
        2500, 4000, 6300, 8000, 10000, 12500, 14000, 16000
    ]

    def __init__(
        self,
        sample_rate: int = 48000,
        fft_size: int = 1024,
        gain: float = 1.0,
        squelch: float = 0.005,
        agc_enabled: bool = True,
        agc_decay: float = 0.992,
        smoothing_factor: float = 0.25,
    ):
        self.sample_rate = sample_rate
        self.fft_size = fft_size
        self.gain = gain
        self.squelch = squelch
        self.agc_enabled = agc_enabled
        self.agc_decay = agc_decay
        self.smoothing_factor = smoothing_factor

        # Precompute window
        self._window = np.hanning(self.fft_size).astype(np.float32)
        self._fft_freqs = np.fft.rfftfreq(self.fft_size, d=1.0 / self.sample_rate)

        # Precompute band bin indices
        self._band_indices = self._compute_band_bins()

        # Pink noise compensation curve (slight high-frequency boost)
        # Higher frequencies naturally have less energy in music
        self._eq_curve = np.array([
            1.0,  # 60 Hz
            1.0,  # 100 Hz
            1.1,  # 160 Hz
            1.2,  # 250 Hz
            1.3,  # 400 Hz
            1.5,  # 630 Hz
            1.8,  # 1000 Hz
            2.2,  # 1600 Hz
            2.8,  # 2500 Hz
            3.5,  # 4000 Hz
            4.2,  # 6300 Hz
            5.0,  # 8000 Hz
            6.0,  # 10000 Hz
            7.0,  # 12500 Hz
            8.0,  # 14000 Hz
            9.0,  # 16000 Hz
        ], dtype=np.float32)

        # State tracking
        self._smoothed_sample = 0.0
        self._max_history = 0.05
        self._bass_energy_history = 0.0
        self._frames_since_peak = 100
        self._min_peak_refractory = 6  # ~120ms at 50fps

    def _compute_band_bins(self) -> List[Tuple[int, int]]:
        """Precomputes start and end FFT bin indices for each of the 16 bands."""
        indices = []
        num_bands = len(self.BAND_CENTERS)
        for i, center in enumerate(self.BAND_CENTERS):
            # Compute geometric or logarithmic boundaries
            if i == 0:
                low = 20.0
            else:
                low = math.sqrt(self.BAND_CENTERS[i - 1] * center)

            if i == num_bands - 1:
                high = 20000.0
            else:
                high = math.sqrt(center * self.BAND_CENTERS[i + 1])

            # Find matching FFT bin slice
            idx_start = int(np.searchsorted(self._fft_freqs, low))
            idx_end = int(np.searchsorted(self._fft_freqs, high))
            # Ensure at least 1 bin per band
            idx_end = max(idx_end, idx_start + 1)
            indices.append((idx_start, min(idx_end, len(self._fft_freqs))))
        return indices

    def process_pcm(self, pcm_data: bytes, channels: int = 2) -> AudioFeatures:
        """Processes raw 16-bit signed PCM audio bytes and extracts features.
        
        Args:
            pcm_data: Raw bytes of signed 16-bit integer PCM.
            channels: 1 for mono, 2 for stereo.
        """
        # Convert bytes to numpy int16 array
        audio = np.frombuffer(pcm_data, dtype=np.int16)
        if len(audio) == 0:
            return AudioFeatures()

        # Downmix stereo to mono if needed
        if channels == 2:
            if len(audio) % 2 != 0:
                audio = audio[:len(audio) - (len(audio) % 2)]
            mono = (audio[0::2].astype(np.float32) + audio[1::2].astype(np.float32)) * 0.5
        else:
            mono = audio.astype(np.float32)

        # Normalize to [-1.0, 1.0]
        mono = mono / 32768.0

        # Adjust buffer size to match fft_size
        if len(mono) < self.fft_size:
            mono = np.pad(mono, (0, self.fft_size - len(mono)), mode='constant')
        elif len(mono) > self.fft_size:
            mono = mono[-self.fft_size:]

        # Calculate RMS energy
        rms = float(np.sqrt(np.mean(mono ** 2)))

        # Squelch / silence check
        if rms < self.squelch:
            self._smoothed_sample *= 0.8
            self._frames_since_peak += 1
            waveform = [float(x) for x in mono[::max(1, len(mono) // 32)][:32]]
            return AudioFeatures(
                sample_raw=0.0,
                sample_smth=float(self._smoothed_sample),
                sample_peak=0,
                fft_result=[0] * 16,
                fft_magnitude=0.0,
                fft_major_peak=0.0,
                rms_energy=rms,
                waveform_preview=waveform
            )

        # Apply Hanning window & calculate Real FFT (normalized to [0.0, 1.0])
        windowed = mono * self._window
        spectrum = np.abs(np.fft.rfft(windowed)) / (self.fft_size / 2.0)

        # Dynamic gain / AGC tracking
        current_max = float(np.max(spectrum)) if len(spectrum) > 0 else 0.001
        if self.agc_enabled:
            # Smooth attack and slow decay to preserve musical transients
            if current_max > self._max_history:
                self._max_history = (0.25 * current_max) + (0.75 * self._max_history)
            else:
                self._max_history = (self._max_history * self.agc_decay) + (current_max * (1.0 - self.agc_decay))
            self._max_history = max(0.02, self._max_history)
            scale = 1.0 / self._max_history
        else:
            scale = 2.0 * self.gain

        # Calculate 16-band GEQ values (0-255)
        band_values = []
        for i, (start_idx, end_idx) in enumerate(self._band_indices):
            bin_slice = spectrum[start_idx:end_idx]
            if len(bin_slice) > 0:
                band_energy = float(np.mean(bin_slice))
            else:
                band_energy = 0.0

            val = band_energy * self._eq_curve[i] * scale * self.gain
            val = min(255, max(0, int(val * 255.0)))
            band_values.append(val)

        # Peak detection: focus on bass energy & spectral flux (bands 0-3: 20-300 Hz)
        bass_energy = float(np.mean(band_values[0:4]))
        sample_peak = 0
        self._frames_since_peak += 1

        # Check for beat / onset pulse
        if (
            bass_energy > 35.0
            and bass_energy > (self._bass_energy_history * 1.25)
            and self._frames_since_peak >= self._min_peak_refractory
        ):
            sample_peak = 1
            self._frames_since_peak = 0

        # Update bass energy moving average
        self._bass_energy_history = (0.7 * self._bass_energy_history) + (0.3 * bass_energy)

        # Compute sample_raw (instantaneous energy level 0-255)
        raw_val = min(255.0, float(np.clip(rms * scale * 255.0 * self.gain, 0.0, 255.0)))

        # Exponential moving average smoothing for sample_smth
        self._smoothed_sample = (
            (self.smoothing_factor * raw_val) +
            ((1.0 - self.smoothing_factor) * self._smoothed_sample)
        )

        # Major peak frequency and magnitude
        max_bin = int(np.argmax(spectrum))
        fft_major_peak = float(self._fft_freqs[max_bin]) if max_bin < len(self._fft_freqs) else 0.0
        fft_magnitude = float(spectrum[max_bin] * scale)

        # Generate 32-sample waveform preview for web UI
        waveform_step = max(1, len(mono) // 32)
        waveform = [round(float(x), 3) for x in mono[::waveform_step][:32]]

        return AudioFeatures(
            sample_raw=raw_val,
            sample_smth=float(self._smoothed_sample),
            sample_peak=sample_peak,
            fft_result=band_values,
            fft_magnitude=fft_magnitude,
            fft_major_peak=fft_major_peak,
            rms_energy=rms,
            waveform_preview=waveform
        )

"""Unit tests for AudioDSP engine."""

import math
import numpy as np
from wledsound.audio.dsp import AudioDSP


def test_dsp_silence_squelch():
    dsp = AudioDSP(sample_rate=48000, fft_size=1024, squelch=0.01)
    silence = bytes(1024 * 4)  # 1024 stereo 16-bit samples of zeroes
    features = dsp.process_pcm(silence, channels=2)

    assert features.sample_raw == 0.0
    assert features.sample_peak == 0
    assert len(features.fft_result) == 16
    assert all(b == 0 for b in features.fft_result)


def test_dsp_sine_wave_band_detection():
    sample_rate = 48000
    fft_size = 1024
    dsp = AudioDSP(sample_rate=sample_rate, fft_size=fft_size, squelch=0.001)

    # Generate 100 Hz tone (matches band 1)
    freq = 100.0
    t = np.linspace(0, fft_size / sample_rate, fft_size, endpoint=False)
    sine = (np.sin(2 * np.pi * freq * t) * 30000).astype(np.int16)

    # Interleave to stereo
    stereo = np.empty((fft_size * 2,), dtype=np.int16)
    stereo[0::2] = sine
    stereo[1::2] = sine

    features = dsp.process_pcm(stereo.tobytes(), channels=2)

    assert len(features.fft_result) == 16
    assert features.sample_raw > 0
    # Band 1 (center 100Hz) should have prominent energy
    assert features.fft_result[1] > 50
    assert abs(features.fft_major_peak - freq) < 50.0


def test_dsp_peak_detection():
    dsp = AudioDSP(sample_rate=48000, fft_size=1024)

    # Feed baseline quiet audio
    t = np.linspace(0, 1024 / 48000, 1024, endpoint=False)
    low_audio = (np.sin(2 * np.pi * 60 * t) * 2000).astype(np.int16)
    stereo_low = np.repeat(low_audio, 2).tobytes()

    for _ in range(10):
        dsp.process_pcm(stereo_low, channels=2)

    # Sudden high amplitude kick drum (50 Hz)
    kick = (np.sin(2 * np.pi * 50 * t) * 32000).astype(np.int16)
    stereo_kick = np.repeat(kick, 2).tobytes()

    features = dsp.process_pcm(stereo_kick, channels=2)
    assert features.sample_peak == 1

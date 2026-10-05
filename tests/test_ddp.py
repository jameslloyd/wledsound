"""Unit tests for DDP client and visualizer engine."""

from wledsound.audio.types import AudioFeatures, TrackInfo
from wledsound.wled.effects import VisualizerEngine


def test_visualizer_engine_album_pulse():
    engine = VisualizerEngine(led_count=30, effect_name="album_pulse")
    features = AudioFeatures(sample_raw=150.0, sample_smth=120.0, sample_peak=1)
    track = TrackInfo(palette=[(255, 0, 0), (0, 255, 0), (0, 0, 255)])

    pixels = engine.render(features, track)
    assert len(pixels) == 30
    for r, g, b in pixels:
        assert 0 <= r <= 255
        assert 0 <= g <= 255
        assert 0 <= b <= 255


def test_visualizer_engine_geq_spectrum():
    engine = VisualizerEngine(led_count=32, effect_name="geq_spectrum")
    features = AudioFeatures(fft_result=[128] * 16)
    track = TrackInfo()

    pixels = engine.render(features, track)
    assert len(pixels) == 32
    assert any(any(c > 0 for c in p) for p in pixels)


def test_visualizer_engine_dj_effects():
    from wledsound.wled.effects import AVAILABLE_EFFECTS
    effect_ids = [e["id"] for e in AVAILABLE_EFFECTS]
    for dj_eff in ["dj_chase", "dj_alternator", "dj_blinder", "dj_beams"]:
        assert dj_eff in effect_ids

    engine = VisualizerEngine(led_count=64)
    features = AudioFeatures(
        sample_raw=240.0,
        sample_smth=200.0,
        sample_peak=1,
        fft_result=[200, 180, 160, 140, 120, 100, 80, 60, 50, 40, 30, 20, 15, 10, 5, 0]
    )
    track = TrackInfo(palette=[(255, 0, 128), (0, 240, 255), (130, 0, 255)])

    # Test all 4 DJ effects render correct frame size and valid RGB colors
    for eff in ["dj_chase", "dj_alternator", "dj_blinder", "dj_beams"]:
        engine.set_effect(eff)
        pixels = engine.render(features, track)
        assert len(pixels) == 64
        for r, g, b in pixels:
            assert 0 <= r <= 255
            assert 0 <= g <= 255
            assert 0 <= b <= 255
        assert any(any(c > 0 for c in p) for p in pixels)


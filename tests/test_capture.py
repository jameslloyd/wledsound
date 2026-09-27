import pytest
from wledsound.audio.capture import AudioCapture


def test_audio_capture_init():
    capture = AudioCapture(
        mode="squeezelite",
        squeezelite_host="192.168.6.5",
        squeezelite_port=3483,
        player_name="WLEDSound",
        mac_address="de:47:2d:67:f3:af",
        sample_rate=44100,
        frame_duration_ms=20,
    )
    assert capture.mode == "squeezelite"
    assert capture.sample_rate == 44100
    assert capture.samples_per_frame == 882
    assert capture.bytes_per_frame == 3528  # 882 * 2 channels * 2 bytes


def test_audio_capture_init_48k():
    capture = AudioCapture(
        mode="squeezelite",
        sample_rate=48000,
        frame_duration_ms=20,
    )
    assert capture.sample_rate == 48000
    assert capture.samples_per_frame == 960
    assert capture.bytes_per_frame == 3840


def test_audio_capture_test_mode():
    chunks = []

    def callback(chunk: bytes):
        chunks.append(chunk)

    capture = AudioCapture(mode="test", sample_rate=48000, frame_duration_ms=20)
    capture.start(callback)
    import time
    time.sleep(0.1)
    capture.stop()

    assert len(chunks) > 0
    assert len(chunks[0]) == 3840

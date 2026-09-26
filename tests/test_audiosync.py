"""Unit tests for WLED AudioSync packet building."""

import struct
from wledsound.audio.types import AudioFeatures
from wledsound.wled.audiosync import AudioSyncSender, UDP_SYNC_HEADER_V2, UDP_SYNC_HEADER_V1


def test_v2_packet_exact_size_and_packing():
    sender = AudioSyncSender()

    features = AudioFeatures(
        sample_raw=128.5,
        sample_smth=110.2,
        sample_peak=1,
        fft_result=[10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140, 150, 160],
        fft_magnitude=45.6,
        fft_major_peak=125.0
    )

    packet = sender.build_v2_packet(features)

    # V2 packed struct MUST be exactly 40 bytes
    assert len(packet) == 40

    # Unpack and verify fields
    header, raw, smth, peak, res, *bands, mag, freq = struct.unpack("<6sffBB16Bff", packet)

    assert header == UDP_SYNC_HEADER_V2  # b"00002\0"
    assert abs(raw - 128.5) < 0.01
    assert abs(smth - 110.2) < 0.01
    assert peak == 1
    assert res == 0
    assert bands == features.fft_result
    assert abs(mag - 45.6) < 0.01
    assert abs(freq - 125.0) < 0.01


def test_v1_packet_packing():
    sender = AudioSyncSender(protocol_version=1)

    features = AudioFeatures(
        sample_raw=100.0,
        sample_smth=90.0,
        sample_peak=0,
        fft_result=[5] * 16,
        fft_magnitude=12.0,
        fft_major_peak=80.0
    )

    packet = sender.build_v1_packet(features)
    assert packet.startswith(UDP_SYNC_HEADER_V1)  # b"00001\0"

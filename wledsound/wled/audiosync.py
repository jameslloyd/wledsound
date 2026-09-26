"""WLED UDP AudioSync sender.

Broadcasts real-time audio sync packets over UDP multicast or unicast to WLED devices
running the AudioReactive usermod, allowing native WLED sound effects to dance to music.
"""

import socket
import struct
import logging
from typing import List, Union
from ..audio.types import AudioFeatures

logger = logging.getLogger(__name__)

# WLED Protocol Constants
UDP_SYNC_HEADER_V2 = b"00002\x00"
UDP_SYNC_HEADER_V1 = b"00001\x00"
DEFAULT_MULTICAST_GROUP = "239.0.0.1"
DEFAULT_SYNC_PORT = 11988


class AudioSyncSender:
    """Encodes and sends WLED audioSync packets to multicast and unicast targets."""

    def __init__(
        self,
        targets: List[str] = None,
        port: int = DEFAULT_SYNC_PORT,
        protocol_version: int = 2,
        multicast_ttl: int = 2,
    ):
        self.targets = targets or [DEFAULT_MULTICAST_GROUP]
        self.port = port
        self.protocol_version = protocol_version
        self.multicast_ttl = multicast_ttl
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        
        # Configure socket options for multicast and broadcast
        try:
            self._socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, self.multicast_ttl)
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except Exception as e:
            logger.warning(f"Could not set multicast/broadcast socket options: {e}")

    def build_v2_packet(self, features: AudioFeatures) -> bytes:
        """Builds 40-byte packed V2 audioSyncPacket.
        
        Struct layout:
        char header[6];         // "00002\0"
        float sampleRaw;        // 4 bytes
        float sampleSmth;       // 4 bytes
        uint8_t samplePeak;     // 1 byte
        uint8_t reserved1;      // 1 byte
        uint8_t fftResult[16];  // 16 bytes
        float FFT_Magnitude;    // 4 bytes
        float FFT_MajorPeak;    // 4 bytes
        """
        # Ensure fftResult has exactly 16 bytes (clipped 0-255)
        bands = (features.fft_result + [0] * 16)[:16]
        bands = [min(255, max(0, int(b))) for b in bands]

        return struct.pack(
            "<6sffBB16Bff",
            UDP_SYNC_HEADER_V2,
            float(features.sample_raw),
            float(features.sample_smth),
            1 if features.sample_peak else 0,
            0,  # reserved1
            *bands,
            float(features.fft_magnitude),
            float(features.fft_major_peak)
        )

    def build_v1_packet(self, features: AudioFeatures) -> bytes:
        """Builds legacy V1 audioSyncPacket for older SR WLED versions."""
        bands = (features.fft_result + [0] * 16)[:16]
        bands = [min(255, max(0, int(b))) for b in bands]
        my_vals = [0] * 32

        return struct.pack(
            "<6s32Bii?16Bdd",
            UDP_SYNC_HEADER_V1,
            *my_vals,
            int(features.sample_smth),
            int(features.sample_raw),
            bool(features.sample_peak),
            *bands,
            float(features.fft_magnitude),
            float(features.fft_major_peak)
        )

    def send_features(self, features: AudioFeatures) -> None:
        """Encodes and transmits an audio packet to all configured target IPs."""
        if self.protocol_version == 1:
            packet = self.build_v1_packet(features)
        else:
            packet = self.build_v2_packet(features)

        for target in self.targets:
            try:
                self._socket.sendto(packet, (target, self.port))
            except Exception as e:
                logger.debug(f"Failed to send AudioSync packet to {target}:{self.port} - {e}")

    def close(self) -> None:
        """Closes the UDP socket."""
        try:
            self._socket.close()
        except Exception:
            pass

"""Distributed Display Protocol (DDP) client for direct pixel streaming to WLED.

Streams real-time 24-bit RGB frames over UDP port 4048 to any standard WLED controller,
enabling custom sound visualizers and album-cover-palette effects without requiring
the AudioReactive usermod.
"""

import socket
import struct
import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)

DEFAULT_DDP_PORT = 4048
DDP_HEADER_LEN = 10
DDP_MAX_PIXELS_PER_PACKET = 480  # 1440 data bytes + 10 byte header = 1450 bytes (< MTU 1500)


class DDPClient:
    """Manages DDP packet packaging and UDP transmission to WLED devices."""

    def __init__(self, targets: List[str] = None, port: int = DEFAULT_DDP_PORT):
        self.targets = targets or []
        self.port = port
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sequence = 0

    def send_frame(self, pixels: List[Tuple[int, int, int]], target_ip: str = None) -> None:
        """Sends an RGB frame to specified target or all configured targets.
        
        Args:
            pixels: List of (r, g, b) tuples, each in range 0-255.
            target_ip: Optional specific IP. If omitted, sends to all targets.
        """
        if not pixels:
            return

        destinations = [target_ip] if target_ip else self.targets
        if not destinations:
            return

        total_pixels = len(pixels)
        offset_pixels = 0

        # Increment sequence counter (cycles 1..15)
        self._sequence = (self._sequence % 15) + 1

        while offset_pixels < total_pixels:
            chunk_size = min(total_pixels - offset_pixels, DDP_MAX_PIXELS_PER_PACKET)
            chunk_pixels = pixels[offset_pixels: offset_pixels + chunk_size]
            is_last = (offset_pixels + chunk_size) >= total_pixels

            # Build raw RGB payload
            payload = bytearray(chunk_size * 3)
            for i, (r, g, b) in enumerate(chunk_pixels):
                payload[i * 3] = min(255, max(0, int(r)))
                payload[i * 3 + 1] = min(255, max(0, int(g)))
                payload[i * 3 + 2] = min(255, max(0, int(b)))

            # DDP Header:
            # Flags: 0x40 (V1) | (0x01 if is_last else 0x00) -> Push flag on final packet
            flags = 0x41 if is_last else 0x40
            data_type = 0x01  # RGB
            dest_id = 0x01
            offset_bytes = offset_pixels * 3
            data_len = len(payload)

            header = struct.pack(
                ">BBBBIH",
                flags,
                self._sequence,
                data_type,
                dest_id,
                offset_bytes,
                data_len
            )
            packet = header + payload

            for target in destinations:
                try:
                    self._socket.sendto(packet, (target, self.port))
                except Exception as e:
                    logger.debug(f"Failed to send DDP packet to {target}:{self.port} - {e}")

            offset_pixels += chunk_size

    def close(self) -> None:
        """Closes the socket."""
        try:
            self._socket.close()
        except Exception:
            pass

"""WLED HTTP JSON API client.

Handles querying WLED status, power management (auto turn on/off with playback),
setting brightness, and pushing album art color palettes directly into WLED segment colors.
"""

import aiohttp
import logging
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger(__name__)


class WLEDClient:
    """Asynchronous client for interacting with WLED JSON API."""

    def __init__(self, host: str, timeout: float = 3.0):
        self.host = host.rstrip("/")
        self.base_url = f"http://{self.host}"
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def get_state(self) -> Optional[Dict[str, Any]]:
        """Fetches current WLED state (power, brightness, segments)."""
        session = await self._get_session()
        try:
            async with session.get(f"{self.base_url}/json/state") as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            logger.debug(f"Failed to fetch state from WLED ({self.host}): {e}")
        return None

    async def get_info(self) -> Optional[Dict[str, Any]]:
        """Fetches WLED device info (LED count, version, name)."""
        session = await self._get_session()
        try:
            async with session.get(f"{self.base_url}/json/info") as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            logger.debug(f"Failed to fetch info from WLED ({self.host}): {e}")
        return None

    async def set_power(self, on: bool) -> bool:
        """Powers WLED on or off."""
        return await self._post_state({"on": on})

    async def set_brightness(self, bri: int) -> bool:
        """Sets WLED master brightness (0-255)."""
        bri = max(0, min(255, bri))
        return await self._post_state({"bri": bri})

    async def set_segment_colors(self, colors: List[Tuple[int, int, int]], segment_id: Optional[int] = None) -> bool:
        """Pushes album art colors to WLED segment primary, secondary, and tertiary colors.
        
        Args:
            colors: List of up to 3 RGB tuples [(r, g, b), ...]
            segment_id: Target segment index, or None to update all active segments.
        """
        if not colors:
            return False

        col_list = [[c[0], c[1], c[2]] for c in colors[:3]]
        # Pad with black if fewer than 3 colors
        while len(col_list) < 3:
            col_list.append([0, 0, 0])

        if segment_id is not None:
            payload = {"seg": [{"id": segment_id, "col": col_list}]}
        else:
            # Apply to all active segments on the device
            state = await self.get_state()
            seg_list = []
            if state and "seg" in state and isinstance(state["seg"], list):
                for s in state["seg"]:
                    if s.get("on", True) is not False:
                        seg_list.append({"id": s["id"], "col": col_list})
            if not seg_list:
                seg_list = [{"id": 0, "col": col_list}]
            payload = {"seg": seg_list}

        return await self._post_state(payload)

    async def set_preset(self, preset_id: int) -> bool:
        """Loads a WLED preset by ID."""
        return await self._post_state({"ps": preset_id})

    async def detect_device_config(self) -> Optional[Dict[str, Any]]:
        """Queries WLED device to auto-discover name, led count, and segments."""
        info = await self.get_info()
        state = await self.get_state()
        if not info or not state:
            return None

        dev_name = info.get("name", self.host)
        led_count = info.get("leds", {}).get("count", 60)
        segments_raw = state.get("seg", [])

        segments = []
        for s in segments_raw:
            segments.append({
                "id": s.get("id", len(segments)),
                "name": s.get("n", f"Segment {s.get('id', len(segments))}"),
                "start": s.get("start", 0),
                "stop": s.get("stop", led_count),
                "effect": "album_pulse",
                "reverse": s.get("rev", False),
                "mirror": s.get("mi", False),
                "brightness": 1.0,
                "palette": None
            })

        return {
            "ip": self.host,
            "name": dev_name,
            "led_count": led_count,
            "ddp_enabled": True,
            "segments": segments
        }

    async def enable_udp_sync(self, receive: bool = True) -> bool:
        """Ensures WLED UDP Sync receive is active."""
        return await self._post_state({"udpn": {"recv": receive}})

    async def _post_state(self, payload: Dict[str, Any]) -> bool:
        """Posts a state payload to /json/state."""
        session = await self._get_session()
        try:
            async with session.post(f"{self.base_url}/json/state", json=payload) as resp:
                return resp.status == 200
        except Exception as e:
            logger.debug(f"Failed to post state to WLED ({self.host}): {e}")
            return False

    async def close(self) -> None:
        """Closes the underlying aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()

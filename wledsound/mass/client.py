"""Music Assistant WebSocket & REST client.

Monitors real-time playback states, active track metadata, album artwork,
and triggers color palette extraction when songs change.
"""

import json
import asyncio
import logging
import aiohttp
from typing import Optional, Callable, Dict, Any
from ..audio.types import TrackInfo
from .palette import PaletteExtractor

logger = logging.getLogger(__name__)


class MusicAssistantClient:
    """Asynchronous client connecting to Music Assistant API for playback tracking."""

    def __init__(
        self,
        server_url: str = "http://127.0.0.1:8095",
        player_id: Optional[str] = None,
        token: Optional[str] = None,
        palette_extractor: Optional[PaletteExtractor] = None
    ):
        self.server_url = server_url.rstrip("/")
        self.ws_url = self.server_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
        self.target_player_id = player_id
        self.token = token
        self.palette_extractor = palette_extractor or PaletteExtractor()

        self.current_track = TrackInfo()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._on_track_changed: Optional[Callable[[TrackInfo], None]] = None
        self._on_state_changed: Optional[Callable[[str], None]] = None

    def set_callbacks(
        self,
        on_track_changed: Optional[Callable[[TrackInfo], None]] = None,
        on_state_changed: Optional[Callable[[str], None]] = None
    ) -> None:
        """Sets event listeners for track changes and playback state updates."""
        self._on_track_changed = on_track_changed
        self._on_state_changed = on_state_changed

    async def start(self) -> None:
        """Starts the background listening task."""
        if self._running:
            return
        self._running = True
        self._session = aiohttp.ClientSession()
        self._task = asyncio.create_task(self._connection_loop())
        logger.info(f"Music Assistant listener started for {self.server_url}")

    async def stop(self) -> None:
        """Stops the client."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._session and not self._session.closed:
            await self._session.close()
        logger.info("Music Assistant listener stopped")

    async def _connection_loop(self) -> None:
        """Reconnection loop for WebSocket connection."""
        backoff = 2.0
        while self._running:
            try:
                headers = {}
                if self.token:
                    headers["Authorization"] = f"Bearer {self.token}"

                logger.debug(f"Connecting to Music Assistant WS: {self.ws_url}")
                async with self._session.ws_connect(self.ws_url, headers=headers) as ws:
                    logger.info("Connected to Music Assistant WebSocket!")
                    backoff = 2.0

                    # Request initial players list
                    cmd_id = 1
                    await ws.send_json({"cmd": "players/all", "message_id": cmd_id})

                    # Listen for messages
                    async for msg in ws:
                        if not self._running:
                            break
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await self._handle_ws_message(msg.data)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Music Assistant connection error: {e}. Retrying in {backoff:.1f}s...")
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 1.5)

    async def _handle_ws_message(self, raw_data: str) -> None:
        """Parses Music Assistant WebSocket frames."""
        try:
            data = json.loads(raw_data)
        except Exception:
            return

        # Handle players/all response or player_updated event
        event_type = data.get("event")
        result = data.get("result")

        if isinstance(result, list):  # Response to players/all
            for player in result:
                if self._matches_player(player):
                    await self._update_from_player_data(player)
                    break

        elif event_type in ("player_updated", "queue_updated"):
            player = data.get("data")
            if isinstance(player, dict) and self._matches_player(player):
                await self._update_from_player_data(player)

    def _matches_player(self, player_data: Dict[str, Any]) -> bool:
        """Determines if the given player dict matches our target."""
        p_id = player_data.get("player_id") or player_data.get("id")
        if not self.target_player_id:
            # If no target configured, match any playing player, or the first available
            return True
        return p_id == self.target_player_id

    async def _update_from_player_data(self, player_data: Dict[str, Any]) -> None:
        """Updates internal state and extracts artwork colors if changed."""
        # Determine state
        raw_state = player_data.get("state", "idle")
        if isinstance(raw_state, str):
            state = raw_state.lower()
        else:
            state = "playing" if player_data.get("powered") else "idle"

        # Media item or current item
        current_item = player_data.get("current_item") or player_data.get("current_media") or {}
        media_item = current_item.get("media_item") or current_item

        title = media_item.get("name") or media_item.get("title") or "Unknown Title"
        artists = media_item.get("artists") or []
        if isinstance(artists, list) and artists:
            artist = artists[0].get("name") if isinstance(artists[0], dict) else str(artists[0])
        else:
            artist = media_item.get("artist") or "Unknown Artist"

        album_obj = media_item.get("album") or {}
        album = album_obj.get("name") if isinstance(album_obj, dict) else str(album_obj) if album_obj else ""

        # Extract image URL
        image_url = None
        images = media_item.get("metadata", {}).get("images") or media_item.get("images") or []
        if isinstance(images, list) and images:
            img = images[0]
            image_url = img.get("path") or img.get("url") if isinstance(img, dict) else str(img)

        # Resolve relative image URL against MA server URL
        if image_url and not image_url.startswith(("http://", "https://")):
            image_url = f"{self.server_url}/{image_url.lstrip('/')}"

        # Check if state changed
        state_changed = (state != self.current_track.state)
        track_changed = (title != self.current_track.title or artist != self.current_track.artist or image_url != self.current_track.image_url)

        self.current_track.state = state
        self.current_track.title = title
        self.current_track.artist = artist
        self.current_track.album = album

        if track_changed:
            self.current_track.image_url = image_url
            logger.info(f"Now Playing: '{title}' by '{artist}' (State: {state})")
            # Extract color palette
            if image_url:
                palette = await self.palette_extractor.extract_from_url(image_url)
                self.current_track.palette = palette
            if self._on_track_changed:
                self._on_track_changed(self.current_track)

        if state_changed and self._on_state_changed:
            self._on_state_changed(state)

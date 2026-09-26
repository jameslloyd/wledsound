"""Music Assistant client with support for official library and token auth."""

import asyncio
import logging
import aiohttp
from typing import Optional, Callable, Dict, Any
from ..audio.types import TrackInfo
from .palette import PaletteExtractor

logger = logging.getLogger(__name__)

try:
    from music_assistant_client import MusicAssistantClient as OfficialMassClient
    from music_assistant_models.errors import AuthenticationRequired
    HAS_OFFICIAL_CLIENT = True
except ImportError:
    HAS_OFFICIAL_CLIENT = False


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
        self.target_player_id = player_id
        self.token = token.strip() if token else None
        self.palette_extractor = palette_extractor or PaletteExtractor()

        self.current_track = TrackInfo()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._on_track_changed: Optional[Callable[[TrackInfo], None]] = None
        self._on_state_changed: Optional[Callable[[str], None]] = None
        self._auth_warning_logged = False
        self._active_playing_player_id: Optional[str] = None

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
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"Music Assistant client started for {self.server_url}")

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
        logger.info("Music Assistant client stopped")

    async def _run_loop(self) -> None:
        """Main connection management loop with exponential backoff."""
        backoff = 4.0
        while self._running:
            try:
                if not self.token:
                    if not self._auth_warning_logged:
                        logger.warning(
                            "Music Assistant server (v2.x) requires an authentication token. "
                            "Create a Long-Lived Token in Music Assistant (Settings -> Core / Authentication) "
                            "and add it to config.yaml under 'music_assistant: token: YOUR_TOKEN' to enable live artwork & metadata. "
                            "Retrying in 20 seconds..."
                        )
                        self._auth_warning_logged = True
                    await asyncio.sleep(20.0)
                    continue

                if HAS_OFFICIAL_CLIENT:
                    await self._run_official_client()
                else:
                    await self._run_raw_ws_client()
                backoff = 4.0
            except asyncio.CancelledError:
                break
            except Exception as e:
                err_msg = str(e)
                if "Authentication" in err_msg or "AuthenticationRequired" in type(e).__name__:
                    if not self._auth_warning_logged:
                        logger.warning(
                            "Music Assistant authentication failed. Please verify your token in config.yaml. "
                            "Retrying in 20 seconds..."
                        )
                        self._auth_warning_logged = True
                    await asyncio.sleep(20.0)
                    continue
                else:
                    logger.warning(f"Music Assistant connection error: {e}. Retrying in {backoff:.1f}s...")

                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 1.5)

    async def _run_official_client(self) -> None:
        """Uses official music-assistant-client library."""
        from music_assistant_models.enums import EventType
        client = OfficialMassClient(self.server_url, None, token=self.token)
        await client.connect()
        logger.info("Successfully connected to Music Assistant via official client!")
        self._auth_warning_logged = False

        # Launch the background listening loop
        listen_task = asyncio.create_task(client.start_listening())
        await asyncio.sleep(0.5)

        # Check currently playing or existing players
        for player in client.players.players:
            if self._matches_player_id(player.player_id):
                await self._update_from_official_player(player, client)
                if str(getattr(player, "playback_state", "")).lower() == "playing":
                    break

        # Define listener callback for player events
        def on_player_event(event):
            try:
                p_id = getattr(event, "object_id", None)
                player = None
                if hasattr(event, "data") and event.data:
                    player = event.data
                elif p_id:
                    player = client.players.get(p_id)

                if player and self._matches_player_id(getattr(player, "player_id", None)):
                    asyncio.create_task(self._update_from_official_player(player, client))
            except Exception as err:
                logger.debug(f"Error handling player event: {err}")

        # Subscribe to player events
        client.subscribe(on_player_event, (EventType.PLAYER_UPDATED, EventType.QUEUE_UPDATED))

        try:
            await listen_task
        finally:
            if not listen_task.done():
                listen_task.cancel()
            await client.disconnect()

    def _matches_player_id(self, player_id: Optional[str]) -> bool:
        if not self.target_player_id or not player_id:
            return True
        return player_id == self.target_player_id

    async def _update_from_official_player(self, player: Any, client: Any) -> None:
        """Extracts track details and artwork from official player object."""
        state = str(getattr(player, "playback_state", "")).lower()
        player_id = getattr(player, "player_id", "")

        # If auto-tracking across multiple players, prioritize the currently active playing player
        if not self.target_player_id:
            if state != "playing" and self._active_playing_player_id and self._active_playing_player_id != player_id:
                active_p = client.players.get(self._active_playing_player_id)
                if active_p and str(getattr(active_p, "playback_state", "")).lower() == "playing":
                    return
            if state == "playing":
                self._active_playing_player_id = player_id

        current_media = getattr(player, "current_media", None)
        title = "Unknown Title"
        artist = "Unknown Artist"
        album = ""
        image_url = None

        if current_media:
            title = getattr(current_media, "title", None) or getattr(current_media, "name", "Unknown Title")
            artist = getattr(current_media, "artist", "Unknown Artist")
            album = getattr(current_media, "album", "")
            image_url = getattr(current_media, "image_url", None)

        if image_url and not image_url.startswith(("http://", "https://")):
            image_url = f"{self.server_url}/{image_url.lstrip('/')}"

        await self._process_track_update(title, artist, album, image_url, state)

    async def _run_raw_ws_client(self) -> None:
        """Fallback raw WebSocket listener if official client is unavailable."""
        ws_url = self.server_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url, headers=headers) as ws:
                logger.info("Connected to Music Assistant WebSocket (raw client)")
                self._auth_warning_logged = False

                # Initial handshake message from MA server
                init_msg = await ws.receive()
                if init_msg.type != aiohttp.WSMsgType.TEXT:
                    logger.warning(f"Unexpected initial WS message: {init_msg}")
                    await asyncio.sleep(5.0)
                    return

                # Send auth if token provided
                if self.token:
                    await ws.send_json({"cmd": "auth", "token": self.token, "message_id": 1})

                # Send get players command
                await ws.send_json({"cmd": "players/all", "message_id": 2})

                async for msg in ws:
                    if not self._running:
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._handle_raw_json(msg.data)
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break

        await asyncio.sleep(3.0)

    async def _handle_raw_json(self, raw_data: str) -> None:
        try:
            data = json.loads(raw_data)
        except Exception:
            return

        result = data.get("result")
        if isinstance(result, list):
            for player in result:
                p_id = player.get("player_id") or player.get("id")
                if self._matches_player_id(p_id):
                    await self._update_from_dict(player)
                    break
        elif data.get("event") in ("player_updated", "queue_updated"):
            player = data.get("data")
            if isinstance(player, dict):
                p_id = player.get("player_id") or player.get("id")
                if self._matches_player_id(p_id):
                    await self._update_from_dict(player)

    async def _update_from_dict(self, player: Dict[str, Any]) -> None:
        state = player.get("state", "idle").lower()
        item = player.get("current_item") or player.get("current_media") or {}
        media = item.get("media_item") or item

        title = media.get("name") or media.get("title") or "Unknown Title"
        artists = media.get("artists") or []
        artist = artists[0].get("name") if (artists and isinstance(artists[0], dict)) else media.get("artist", "Unknown Artist")
        album = media.get("album", {}).get("name", "") if isinstance(media.get("album"), dict) else str(media.get("album", ""))

        image_url = None
        images = media.get("metadata", {}).get("images") or media.get("images") or []
        if images and isinstance(images, list):
            img = images[0]
            image_url = img.get("path") or img.get("url") if isinstance(img, dict) else str(img)

        if image_url and not image_url.startswith(("http://", "https://")):
            image_url = f"{self.server_url}/{image_url.lstrip('/')}"

        await self._process_track_update(title, artist, album, image_url, state)

    async def _process_track_update(
        self, title: str, artist: str, album: str, image_url: Optional[str], state: str
    ) -> None:
        state_changed = (state != self.current_track.state)
        track_changed = (
            title != self.current_track.title or
            artist != self.current_track.artist or
            image_url != self.current_track.image_url
        )

        self.current_track.state = state
        self.current_track.title = title
        self.current_track.artist = artist
        self.current_track.album = album

        if track_changed:
            self.current_track.image_url = image_url
            logger.info(f"Now Playing: '{title}' by '{artist}' (State: {state})")
            if image_url:
                palette = await self.palette_extractor.extract_from_url(image_url)
                self.current_track.palette = palette
            if self._on_track_changed:
                self._on_track_changed(self.current_track)

        if state_changed and self._on_state_changed:
            self._on_state_changed(state)

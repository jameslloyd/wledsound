"""Main application orchestrator and service coordinator for WLEDSound."""

import os
import sys
import time
import signal
import asyncio
import logging
import uvicorn
from typing import Dict, Any, Optional, Callable, List

from .config import AppConfig, load_config, save_config, WLEDDeviceConfig, WLEDSegmentConfig
from .audio.types import AudioFeatures, TrackInfo
from .audio.dsp import AudioDSP
from .audio.capture import AudioCapture
from .wled.audiosync import AudioSyncSender
from .wled.ddp import DDPClient
from .wled.effects import VisualizerEngine, get_palette_definitions, BUILTIN_PALETTES, AVAILABLE_EFFECTS
from .wled.client import WLEDClient
from .mass.client import MusicAssistantClient
from .mass.palette import PaletteExtractor
from .web.app import create_web_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("wledsound")


class ServiceCoordinator:
    """Central orchestrator managing audio ingest, DSP analysis, WLED outputs, and MA sync."""

    def __init__(self, config: Optional[AppConfig] = None, config_path: str = "config.yaml"):
        if config is None:
            config = load_config(config_path)
        self.config = config
        self.config_path = config_path

        # State
        self.current_features = AudioFeatures()
        self.current_track = TrackInfo()
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._telemetry_broadcaster: Optional[Callable[[Dict[str, Any]], Any]] = None
        self._last_telemetry_time = 0.0
        self._last_audio_chunk_time = 0.0
        self._heartbeat_task: Optional[asyncio.Task] = None

        # Initialize Subsystems
        self._init_dsp()
        self._init_wled()
        self._init_mass()
        self._init_capture()

    def _init_dsp(self) -> None:
        self.dsp = AudioDSP(
            sample_rate=self.config.audio.sample_rate,
            fft_size=1024,
            gain=self.config.audio.gain,
            squelch=self.config.audio.squelch,
            agc_enabled=self.config.audio.agc_enabled,
            smoothing_factor=self.config.audio.smoothing
        )

    def _init_wled(self) -> None:
        # AudioSync UDP Sender
        self.audiosync = AudioSyncSender(
            targets=self.config.wled.audiosync_targets,
            port=self.config.wled.audiosync_port,
            protocol_version=self.config.wled.protocol_version
        )
        # DDP Client & Visualizer
        self.ddp = DDPClient(
            targets=self.config.wled.ddp_targets,
            port=self.config.wled.ddp_port
        )
        self.visualizer = VisualizerEngine(
            led_count=self.config.wled.led_count,
            effect_name=self.config.wled.ddp_effect,
            palette_name=self.config.wled.palette
        )
        # WLED HTTP Clients (all devices + wled_hosts)
        self.wled_clients: Dict[str, WLEDClient] = {}
        all_hosts = set(self.config.wled.wled_hosts)
        for dev in self.config.wled.devices:
            all_hosts.add(dev.ip)
        for host in all_hosts:
            if host and host != "239.0.0.1":
                self.wled_clients[host] = WLEDClient(host)
        primary_host = self.config.wled.wled_hosts[0] if self.config.wled.wled_hosts else None
        if not primary_host and self.wled_clients:
            primary_host = next(iter(self.wled_clients.keys()))
        self.wled_client = self.wled_clients.get(primary_host) if primary_host else None

    def _init_mass(self) -> None:
        self.palette_extractor = PaletteExtractor()
        if self.config.music_assistant.enabled:
            auto_group_id = (
                self.config.audio.squeezelite_mac
                if (self.config.audio.mode == "squeezelite" and self.config.audio.auto_group)
                else None
            )
            self.mass_client = MusicAssistantClient(
                server_url=self.config.music_assistant.server_url,
                player_id=self.config.music_assistant.player_id,
                token=self.config.music_assistant.token,
                palette_extractor=self.palette_extractor,
                auto_group_player_id=auto_group_id
            )
            self.mass_client.set_callbacks(
                on_track_changed=self._on_track_changed,
                on_state_changed=self._on_playback_state_changed
            )
        else:
            self.mass_client = None

    def _init_capture(self) -> None:
        self.capture = AudioCapture(
            mode=self.config.audio.mode,
            squeezelite_host=self.config.audio.squeezelite_host,
            squeezelite_port=self.config.audio.squeezelite_port,
            player_name=self.config.audio.squeezelite_player_name,
            mac_address=self.config.audio.squeezelite_mac,
            snapserver_host=self.config.audio.snapserver_host,
            snapserver_port=self.config.audio.snapserver_port,
            fifo_path=self.config.audio.fifo_path,
            sample_rate=self.config.audio.sample_rate
        )

    def set_telemetry_broadcaster(self, broadcaster: Callable[[Dict[str, Any]], Any]) -> None:
        self._telemetry_broadcaster = broadcaster

    def get_telemetry_frame(self) -> Dict[str, Any]:
        """Returns the current telemetry frame for WebSocket clients."""
        features = self.current_features
        mass_online = bool(
            self.mass_client
            and self.current_track.state
            and self.current_track.state not in ("", "offline", "unauthenticated")
        )
        active_palette = self.visualizer.get_palette(self.current_track.palette)
        return {
            "bands": features.fft_result,
            "sample_raw": round(features.sample_raw, 1),
            "sample_smth": round(features.sample_smth, 1),
            "sample_peak": features.sample_peak,
            "major_peak": round(features.fft_major_peak, 1),
            "rms": round(features.rms_energy, 4),
            "waveform": features.waveform_preview,
            "mass_connected": mass_online,
            "audio_active": (time.perf_counter() - self._last_audio_chunk_time < 1.0),
            "sync_enabled": self.config.wled.sync_enabled,
            "wled_mode": self.config.wled.mode,
            "palette_mode": self.visualizer.palette_name,
            "active_palette": [list(c) for c in active_palette],
            "available_palettes": get_palette_definitions(),
            "available_effects": AVAILABLE_EFFECTS,
            "devices": [d.model_dump() for d in self.config.wled.devices],
            "track": {
                "title": self.current_track.title,
                "artist": self.current_track.artist,
                "album": self.current_track.album,
                "state": self.current_track.state,
                "image_url": self.current_track.image_url,
                "palette": self.current_track.palette
            }
        }

    def _broadcast_frame(self, frame_data: Dict[str, Any]) -> None:
        if self._telemetry_broadcaster and self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._telemetry_broadcaster(frame_data), self._loop
            )

    def _on_audio_chunk(self, pcm_bytes: bytes) -> None:
        """Fast real-time audio chunk processor invoked every ~20ms."""
        self._last_audio_chunk_time = time.perf_counter()

        # 1. DSP Analysis (always compute features for live web UI visualizer)
        features = self.dsp.process_pcm(pcm_bytes)
        self.current_features = features

        # Skip WLED hardware packet streaming if sync is disabled or mode is off
        if not self.config.wled.sync_enabled or self.config.wled.mode.lower() == "off":
            now = time.perf_counter()
            if self._telemetry_broadcaster and (now - self._last_telemetry_time >= 0.025):
                self._last_telemetry_time = now
                self._broadcast_frame(self.get_telemetry_frame())
            return

        mode = self.config.wled.mode.lower()

        # 2. AudioSync UDP Multicast / Unicast (if hybrid or audiosync mode)
        if mode in ("hybrid", "audiosync"):
            self.audiosync.send_features(features)

        # 3. DDP Real-time Pixel Streaming (if hybrid or ddp mode)
        if mode in ("hybrid", "ddp"):
            active_palette = self.visualizer.get_palette(self.current_track.palette)
            if self.config.wled.devices:
                for dev in self.config.wled.devices:
                    if dev.ddp_enabled:
                        pixels = self.visualizer.render_device(
                            dev, features, self.current_track, active_palette
                        )
                        self.ddp.send_frame(pixels, target_ip=dev.ip)
            elif self.config.wled.ddp_targets:
                pixels = self.visualizer.render(features, self.current_track)
                self.ddp.send_frame(pixels)

        # 4. Web Telemetry Broadcast (~30-50 fps limit)
        now = time.perf_counter()
        if self._telemetry_broadcaster and (now - self._last_telemetry_time >= 0.025):
            self._last_telemetry_time = now
            self._broadcast_frame(self.get_telemetry_frame())

    def _on_track_changed(self, track: TrackInfo) -> None:
        """Invoked when Music Assistant changes tracks."""
        self.current_track = track
        logger.info(f"Updated track: {track.title} by {track.artist}")

        # Broadcast update to web visualizer immediately
        self._broadcast_frame(self.get_telemetry_frame())

        # Sync palette to WLED segment colors if enabled
        if self.config.wled.sync_album_art_colors and self.wled_clients:
            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(self.sync_palette_to_wled(), self._loop)

    def _on_playback_state_changed(self, state: str) -> None:
        """Invoked when Music Assistant playback state changes."""
        logger.info(f"Playback state changed to: {state}")
        self._broadcast_frame(self.get_telemetry_frame())

        if not self.config.wled.auto_power or not self.wled_clients:
            return

        if self._loop and self._loop.is_running():
            if state == "playing":
                for client in self.wled_clients.values():
                    asyncio.run_coroutine_threadsafe(client.set_power(True), self._loop)
            elif state in ("paused", "idle", "stopped"):
                logger.info("Music paused/stopped; maintaining idle state")

    async def _idle_heartbeat_loop(self) -> None:
        """Periodic heartbeat broadcast ensuring UI stays in sync even when no audio is streaming."""
        while self._running:
            await asyncio.sleep(0.5)
            # If no audio chunks received in the last 0.5s, send idle telemetry frame
            if time.perf_counter() - self._last_audio_chunk_time >= 0.4:
                self.current_features.sample_peak = 0
                frame = self.get_telemetry_frame()
                if self._telemetry_broadcaster:
                    await self._telemetry_broadcaster(frame)

    async def toggle_wled_power(self) -> bool:
        """Toggles WLED power on/off across all configured devices."""
        if not self.wled_clients:
            return False
        current_state = await self.wled_client.get_state() if self.wled_client else None
        is_on = current_state.get("on", False) if current_state else True
        new_state = not is_on
        results = await asyncio.gather(
            *[client.set_power(new_state) for client in self.wled_clients.values()],
            return_exceptions=True
        )
        return any(r is True for r in results)

    def trigger_test_flash(self) -> None:
        """Triggers an instantaneous visual test pulse."""
        flash_features = AudioFeatures(
            sample_raw=255.0,
            sample_smth=220.0,
            sample_peak=1,
            fft_result=[220, 240, 255, 200, 180, 160, 140, 120, 100, 80, 60, 50, 40, 30, 20, 10],
            fft_magnitude=1.0,
            fft_major_peak=120.0,
            rms_energy=0.85,
            waveform_preview=[0.8, -0.8] * 16
        )

        if self.config.wled.mode in ("hybrid", "audiosync"):
            self.audiosync.send_features(flash_features)
        if self.config.wled.mode in ("hybrid", "ddp") and self.config.wled.ddp_targets:
            pixels = [(255, 255, 255)] * self.config.wled.led_count
            self.ddp.send_frame(pixels)

        # Broadcast one-off flash frame to web clients
        frame = self.get_telemetry_frame()
        frame["sample_peak"] = 1
        frame["sample_raw"] = 255.0
        frame["bands"] = flash_features.fft_result
        self._broadcast_frame(frame)

    async def sync_palette_to_wled(self) -> bool:
        """Pushes configured palettes to all WLED devices and their segments."""
        if not self.wled_clients:
            return False
        global_palette = self.visualizer.get_palette(self.current_track.palette)
        tasks = []
        for dev in self.config.wled.devices:
            client = self.wled_clients.get(dev.ip)
            if not client:
                continue
            if dev.segments:
                seg_map = {}
                for seg in dev.segments:
                    if seg.palette and seg.palette != "inherit":
                        seg_pal = self.visualizer.get_palette_by_name(seg.palette, self.current_track.palette)
                    else:
                        seg_pal = global_palette
                    seg_map[seg.id] = seg_pal
                tasks.append(client.set_multi_segment_colors(seg_map))
            else:
                tasks.append(client.set_segment_colors(global_palette))

        for host, client in self.wled_clients.items():
            if not any(d.ip == host for d in self.config.wled.devices):
                tasks.append(client.set_segment_colors(global_palette))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            return any(r is True for r in results)
        return False

    def set_palette(self, palette_name: str) -> None:
        """Sets active color palette, notifies visualizer, saves config, and pushes to WLED."""
        palette_name = palette_name.lower()
        self.config.wled.palette = palette_name
        self.visualizer.set_palette(palette_name)
        logger.info(f"Switched palette to: {palette_name}")
        self._broadcast_frame(self.get_telemetry_frame())
        save_config(self.config, self.config_path)

        if self.config.wled.sync_album_art_colors and self.wled_clients:
            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(self.sync_palette_to_wled(), self._loop)

    def get_devices(self) -> List[Dict[str, Any]]:
        """Returns list of configured devices with segments."""
        return [d.model_dump() for d in self.config.wled.devices]

    async def discover_devices(self) -> List[Dict[str, Any]]:
        """Queries configured hosts to auto-discover WLED device info and segments."""
        all_hosts = list(dict.fromkeys(
            self.config.wled.wled_hosts + [d.ip for d in self.config.wled.devices] + self.config.wled.ddp_targets
        ))
        discovered = []
        for host in all_hosts:
            if not host or host == "239.0.0.1":
                continue
            client = self.wled_clients.get(host) or WLEDClient(host)
            self.wled_clients[host] = client
            dev_dict = await client.detect_device_config()
            if dev_dict:
                existing_dev = next((d for d in self.config.wled.devices if d.ip == host), None)
                if existing_dev:
                    existing_segs = {s.id: s for s in existing_dev.segments}
                    for seg_dict in dev_dict["segments"]:
                        if seg_dict["id"] in existing_segs:
                            prev = existing_segs[seg_dict["id"]]
                            seg_dict["effect"] = prev.effect
                            seg_dict["reverse"] = prev.reverse
                            seg_dict["mirror"] = prev.mirror
                            seg_dict["brightness"] = prev.brightness
                            seg_dict["palette"] = prev.palette
                discovered.append(WLEDDeviceConfig(**dev_dict))

        if discovered:
            self.config.wled.devices = discovered
            for d in discovered:
                if d.ip not in self.config.wled.wled_hosts:
                    self.config.wled.wled_hosts.append(d.ip)
                if d.ddp_enabled and d.ip not in self.config.wled.ddp_targets:
                    self.config.wled.ddp_targets.append(d.ip)
            save_config(self.config, self.config_path)
            self._broadcast_frame(self.get_telemetry_frame())

        return [d.model_dump() for d in self.config.wled.devices]

    def update_segment(self, device_ip: str, segment_id: int, patch: Dict[str, Any]) -> bool:
        """Updates a specific segment on a WLED device."""
        for dev in self.config.wled.devices:
            if dev.ip == device_ip:
                for seg in dev.segments:
                    if seg.id == segment_id:
                        for k, v in patch.items():
                            if hasattr(seg, k):
                                setattr(seg, k, v)
                        save_config(self.config, self.config_path)
                        self._broadcast_frame(self.get_telemetry_frame())

                        # If palette was updated, also push segment color to WLED hardware
                        if "palette" in patch and self._loop and self._loop.is_running():
                            client = self.wled_clients.get(device_ip)
                            if client:
                                global_pal = self.visualizer.get_palette(self.current_track.palette)
                                seg_pal = self.visualizer.get_palette_by_name(seg.palette, self.current_track.palette) if (seg.palette and seg.palette != "inherit") else global_pal
                                asyncio.run_coroutine_threadsafe(
                                    client.set_segment_colors(seg_pal, segment_id=segment_id),
                                    self._loop
                                )
                        return True
        return False

    def update_devices_config(self, devices_data: List[Dict[str, Any]]) -> None:
        """Overwrites devices configuration with new data."""
        self.config.wled.devices = [WLEDDeviceConfig(**d) for d in devices_data]
        save_config(self.config, self.config_path)
        self._broadcast_frame(self.get_telemetry_frame())

    def apply_config_patch(self, patch: Dict[str, Any]) -> None:
        """Applies dynamic runtime configuration updates from API."""
        if "audio" in patch:
            for k, v in patch["audio"].items():
                if hasattr(self.config.audio, k):
                    setattr(self.config.audio, k, v)
            # Update live DSP parameters
            self.dsp.gain = self.config.audio.gain
            self.dsp.squelch = self.config.audio.squelch
            self.dsp.smoothing_factor = self.config.audio.smoothing
            # Restart capture if mode changed
            if "mode" in patch["audio"]:
                self.capture.stop()
                self.capture.mode = self.config.audio.mode
                self.capture.start(self._on_audio_chunk)

        if "wled" in patch:
            for k, v in patch["wled"].items():
                if hasattr(self.config.wled, k):
                    setattr(self.config.wled, k, v)
            self.visualizer.set_effect(self.config.wled.ddp_effect)
            self.visualizer.set_led_count(self.config.wled.led_count)
            if "palette" in patch["wled"]:
                self.set_palette(patch["wled"]["palette"])
            if "devices" in patch["wled"]:
                self.update_devices_config(patch["wled"]["devices"])

        # Save updated config
        save_config(self.config, self.config_path)

    def toggle_sync(self, enabled: Optional[bool] = None) -> bool:
        """Toggles or explicitly sets the LED audio synchronization state."""
        if enabled is None:
            self.config.wled.sync_enabled = not self.config.wled.sync_enabled
        else:
            self.config.wled.sync_enabled = bool(enabled)
        logger.info(f"LED synchronization is now: {'ENABLED' if self.config.wled.sync_enabled else 'PAUSED/OFF'}")
        save_config(self.config, self.config_path)
        self._broadcast_frame(self.get_telemetry_frame())
        return self.config.wled.sync_enabled

    def get_status(self) -> Dict[str, Any]:
        """Returns comprehensive status dictionary."""
        return {
            "audio": {
                "mode": self.config.audio.mode,
                "gain": self.config.audio.gain,
                "squelch": self.config.audio.squelch,
                "smoothing": self.config.audio.smoothing,
                "rms": round(self.current_features.rms_energy, 4)
            },
            "wled": {
                "sync_enabled": self.config.wled.sync_enabled,
                "mode": self.config.wled.mode,
                "audiosync_targets": self.config.wled.audiosync_targets,
                "ddp_targets": self.config.wled.ddp_targets,
                "led_count": self.config.wled.led_count,
                "effect": self.config.wled.ddp_effect,
                "palette": self.config.wled.palette,
                "devices": [d.model_dump() for d in self.config.wled.devices],
                "available_effects": AVAILABLE_EFFECTS,
                "available_palettes": get_palette_definitions()
            },
            "music_assistant": {
                "enabled": self.config.music_assistant.enabled,
                "server_url": self.config.music_assistant.server_url,
                "state": self.current_track.state,
                "title": self.current_track.title,
                "artist": self.current_track.artist,
                "palette": self.current_track.palette
            }
        }

    async def start(self) -> None:
        """Starts all components."""
        self._running = True
        self._loop = asyncio.get_running_loop()

        # Start Audio Ingestion
        self.capture.start(self._on_audio_chunk)

        # Start Music Assistant Listener
        if self.mass_client:
            await self.mass_client.start()

        # Start idle heartbeat task
        self._heartbeat_task = asyncio.create_task(self._idle_heartbeat_loop())

        logger.info(f"WLEDSound initialized successfully in mode '{self.config.wled.mode}'")

    async def stop(self) -> None:
        """Stops all components cleanly."""
        self._running = False
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        self.capture.stop()
        if self.mass_client:
            await self.mass_client.stop()
        self.audiosync.close()
        self.ddp.close()
        if self.wled_client:
            await self.wled_client.close()
        logger.info("WLEDSound stopped cleanly")


async def main_async(config_path: str = "config.yaml"):
    """Async main orchestrator."""
    config = load_config(config_path)
    coordinator = ServiceCoordinator(config, config_path)
    await coordinator.start()

    # Web Dashboard Server
    web_app = create_web_app(coordinator)
    server_config = uvicorn.Config(
        app=web_app,
        host=config.web.host,
        port=config.web.port,
        log_level="warning"
    )
    server = uvicorn.Server(server_config)

    # Handle termination signals
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def handle_signal():
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            pass

    # Run web server
    server_task = asyncio.create_task(server.serve())

    await stop_event.wait()
    logger.info("Shutdown initiated...")
    server.should_exit = True
    await server_task
    await coordinator.stop()


def main():
    """CLI entrypoint."""
    config_file = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    try:
        asyncio.run(main_async(config_file))
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()

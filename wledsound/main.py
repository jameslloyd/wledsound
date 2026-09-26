"""Main application orchestrator and service coordinator for WLEDSound."""

import os
import sys
import time
import signal
import asyncio
import logging
import uvicorn
from typing import Dict, Any, Optional, Callable

from .config import AppConfig, load_config, save_config
from .audio.types import AudioFeatures, TrackInfo
from .audio.dsp import AudioDSP
from .audio.capture import AudioCapture
from .wled.audiosync import AudioSyncSender
from .wled.ddp import DDPClient
from .wled.effects import VisualizerEngine
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

    def __init__(self, config: AppConfig, config_path: str = "config.yaml"):
        self.config = config
        self.config_path = config_path

        # State
        self.current_features = AudioFeatures()
        self.current_track = TrackInfo()
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._telemetry_broadcaster: Optional[Callable[[Dict[str, Any]], Any]] = None
        self._last_telemetry_time = 0.0

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
            effect_name=self.config.wled.ddp_effect
        )
        # WLED HTTP Clients (primary host)
        primary_host = self.config.wled.wled_hosts[0] if self.config.wled.wled_hosts else None
        self.wled_client = WLEDClient(primary_host) if primary_host else None

    def _init_mass(self) -> None:
        self.palette_extractor = PaletteExtractor()
        if self.config.music_assistant.enabled:
            self.mass_client = MusicAssistantClient(
                server_url=self.config.music_assistant.server_url,
                player_id=self.config.music_assistant.player_id,
                token=self.config.music_assistant.token,
                palette_extractor=self.palette_extractor
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
            snapserver_host=self.config.audio.snapserver_host,
            snapserver_port=self.config.audio.snapserver_port,
            fifo_path=self.config.audio.fifo_path,
            sample_rate=self.config.audio.sample_rate
        )

    def set_telemetry_broadcaster(self, broadcaster: Callable[[Dict[str, Any]], Any]) -> None:
        self._telemetry_broadcaster = broadcaster

    def _on_audio_chunk(self, pcm_bytes: bytes) -> None:
        """Fast real-time audio chunk processor invoked every ~20ms."""
        # 1. DSP Analysis
        features = self.dsp.process_pcm(pcm_bytes)
        self.current_features = features

        mode = self.config.wled.mode.lower()

        # 2. AudioSync UDP Multicast / Unicast (if hybrid or audiosync mode)
        if mode in ("hybrid", "audiosync"):
            self.audiosync.send_features(features)

        # 3. DDP Real-time Pixel Streaming (if hybrid or ddp mode)
        if mode in ("hybrid", "ddp") and self.config.wled.ddp_targets:
            pixels = self.visualizer.render(features, self.current_track)
            self.ddp.send_frame(pixels)

        # 4. Web Telemetry Broadcast (~30-50 fps limit)
        now = time.perf_counter()
        if self._telemetry_broadcaster and (now - self._last_telemetry_time >= 0.025):
            self._last_telemetry_time = now
            frame_data = {
                "bands": features.fft_result,
                "sample_raw": round(features.sample_raw, 1),
                "sample_smth": round(features.sample_smth, 1),
                "sample_peak": features.sample_peak,
                "major_peak": round(features.fft_major_peak, 1),
                "rms": round(features.rms_energy, 4),
                "waveform": features.waveform_preview,
                "track": {
                    "title": self.current_track.title,
                    "artist": self.current_track.artist,
                    "album": self.current_track.album,
                    "state": self.current_track.state,
                    "image_url": self.current_track.image_url,
                    "palette": self.current_track.palette
                }
            }
            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._telemetry_broadcaster(frame_data), self._loop
                )

    def _on_track_changed(self, track: TrackInfo) -> None:
        """Invoked when Music Assistant changes tracks."""
        self.current_track = track
        logger.info(f"Updated track: {track.title} by {track.artist}")

        # Sync palette to WLED segment colors if enabled
        if self.config.wled.sync_album_art_colors and self.wled_client and track.palette:
            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self.wled_client.set_segment_colors(track.palette), self._loop
                )

    def _on_playback_state_changed(self, state: str) -> None:
        """Invoked when Music Assistant playback state changes."""
        logger.info(f"Playback state changed to: {state}")
        if not self.config.wled.auto_power or not self.wled_client:
            return

        if self._loop and self._loop.is_running():
            if state == "playing":
                asyncio.run_coroutine_threadsafe(self.wled_client.set_power(True), self._loop)
            elif state in ("paused", "idle", "stopped"):
                # You can choose to turn off or lower brightness
                logger.info("Music paused/stopped; maintaining idle state")

    async def toggle_wled_power(self) -> bool:
        """Toggles WLED power on/off."""
        if not self.wled_client:
            return False
        state = await self.wled_client.get_state()
        if state:
            current_on = state.get("on", False)
            return await self.wled_client.set_power(not current_on)
        return False

    def trigger_test_flash(self) -> None:
        """Triggers an instantaneous visual test pulse."""
        self.current_features.sample_peak = 1
        self.current_features.sample_raw = 255.0
        self.current_features.sample_smth = 200.0

        if self.config.wled.mode in ("hybrid", "audiosync"):
            self.audiosync.send_features(self.current_features)
        if self.config.wled.mode in ("hybrid", "ddp") and self.config.wled.ddp_targets:
            pixels = [(255, 255, 255)] * self.config.wled.led_count
            self.ddp.send_frame(pixels)

    async def sync_palette_to_wled(self) -> bool:
        """Pushes current palette to WLED hardware."""
        if self.wled_client and self.current_track.palette:
            return await self.wled_client.set_segment_colors(self.current_track.palette)
        return False

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

        # Save updated config
        save_config(self.config, self.config_path)

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
                "mode": self.config.wled.mode,
                "audiosync_targets": self.config.wled.audiosync_targets,
                "ddp_targets": self.config.wled.ddp_targets,
                "led_count": self.config.wled.led_count,
                "effect": self.config.wled.ddp_effect
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

        logger.info(f"WLEDSound initialized successfully in mode '{self.config.wled.mode}'")

    async def stop(self) -> None:
        """Stops all components cleanly."""
        self._running = False
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

"""Audio capture manager for Snapcast client, FIFO, and synthetic test generator."""

import os
import time
import math
import select
import logging
import asyncio
import subprocess
import threading
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class AudioCapture:
    """Manages audio ingestion from squeezelite subprocess, snapclient, named pipe FIFO, or test generator."""

    def __init__(
        self,
        mode: str = "squeezelite",
        squeezelite_host: str = "127.0.0.1",
        squeezelite_port: int = 3483,
        player_name: str = "WLEDSound",
        mac_address: str = "de:47:2d:67:f3:af",
        snapserver_host: str = "127.0.0.1",
        snapserver_port: int = 1704,
        fifo_path: str = "/tmp/snapfifo",
        sample_rate: int = 48000,
        channels: int = 2,
        frame_duration_ms: int = 20,
    ):
        self.mode = mode.lower()
        self.squeezelite_host = squeezelite_host
        self.squeezelite_port = squeezelite_port
        self.player_name = player_name
        self.mac_address = mac_address
        self.snapserver_host = snapserver_host
        self.snapserver_port = snapserver_port
        self.fifo_path = fifo_path
        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_duration_ms = frame_duration_ms

        # Calculate bytes per frame: (sample_rate * duration_sec) * channels * 2 bytes/sample
        self.samples_per_frame = int(self.sample_rate * (self.frame_duration_ms / 1000.0))
        self.bytes_per_frame = self.samples_per_frame * self.channels * 2

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._process: Optional[subprocess.Popen] = None
        self._callback: Optional[Callable[[bytes], None]] = None

    def start(self, callback: Callable[[bytes], None]) -> None:
        """Starts the audio capture thread."""
        if self._running:
            return
        self._callback = callback
        self._running = True
        self._thread = threading.Thread(target=self._worker_loop, daemon=True, name="AudioCaptureWorker")
        self._thread.start()
        logger.info(f"Audio capture started in mode '{self.mode}' ({self.bytes_per_frame} bytes/frame)")

    def stop(self) -> None:
        """Stops the audio capture thread and closes any child process."""
        self._running = False
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=1.0)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        logger.info("Audio capture stopped")

    def _worker_loop(self) -> None:
        """Main background loop directing to specific capture modes."""
        while self._running:
            try:
                if self.mode == "squeezelite":
                    self._run_squeezelite()
                elif self.mode == "snapclient":
                    self._run_snapclient()
                elif self.mode == "fifo":
                    self._run_fifo()
                elif self.mode == "test":
                    self._run_test_generator()
                else:
                    logger.warning(f"Unknown mode '{self.mode}', falling back to test generator")
                    self._run_test_generator()
            except Exception as e:
                logger.error(f"Error in audio capture loop: {e}", exc_info=True)
                time.sleep(1.0)

    def _run_squeezelite(self) -> None:
        """Launches squeezelite and streams PCM bytes from its stdout."""
        cmd = [
            "squeezelite",
            "-s", f"{self.squeezelite_host}:{self.squeezelite_port}",
            "-n", self.player_name,
            "-m", self.mac_address,
            "-o", "-",
            "-a", "16",
            "-b", "2048:3445",
            "-f", "/tmp/squeezelite.log",
            "-d", "all=info"
        ]
        logger.info(f"Spawning squeezelite: {' '.join(cmd)}")
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=self.bytes_per_frame * 4
            )
        except FileNotFoundError:
            logger.error("squeezelite binary not found in PATH! Falling back to synthetic test generator.")
            self.mode = "test"
            return

        frame_interval = self.frame_duration_ms / 1000.0
        while self._running and self._process.poll() is None:
            if not self._process.stdout:
                break
            start_t = time.perf_counter()
            raw_chunk = self._process.stdout.read(self.bytes_per_frame)
            if not raw_chunk:
                break
            if len(raw_chunk) == self.bytes_per_frame and self._callback:
                self._callback(raw_chunk)

            # Pace reads at real-time rate (20ms) because stdout pipe has no hardware DAC clock
            elapsed = time.perf_counter() - start_t
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        if self._process:
            logger.warning(f"squeezelite exited with code {self._process.returncode}. Reconnecting in 2s...")
            time.sleep(2.0)

    def _run_snapclient(self) -> None:
        """Launches snapclient and streams PCM bytes from its stdout."""
        cmd = [
            "snapclient",
            "-h", self.snapserver_host,
            "-p", str(self.snapserver_port),
            "--player", "file:filename=stdout",
            "--logsink", "null"
        ]
        logger.info(f"Spawning snapclient: {' '.join(cmd)}")
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=self.bytes_per_frame * 4
            )
        except FileNotFoundError:
            logger.error("snapclient binary not found in PATH! Falling back to synthetic test generator.")
            self.mode = "test"
            return

        while self._running and self._process.poll() is None:
            if not self._process.stdout:
                break
            raw_chunk = self._process.stdout.read(self.bytes_per_frame)
            if not raw_chunk:
                break
            if len(raw_chunk) == self.bytes_per_frame and self._callback:
                self._callback(raw_chunk)

        if self._process:
            logger.warning(f"snapclient exited with code {self._process.returncode}. Reconnecting in 2s...")
            time.sleep(2.0)

    def _run_fifo(self) -> None:
        """Reads PCM audio from a named pipe / FIFO."""
        if not os.path.exists(self.fifo_path):
            logger.warning(f"FIFO path '{self.fifo_path}' does not exist. Waiting...")
            time.sleep(2.0)
            return

        logger.info(f"Opening FIFO at {self.fifo_path}")
        with open(self.fifo_path, "rb") as f:
            while self._running:
                raw_chunk = f.read(self.bytes_per_frame)
                if not raw_chunk:
                    time.sleep(0.01)
                    continue
                if len(raw_chunk) == self.bytes_per_frame and self._callback:
                    self._callback(raw_chunk)

    def _run_test_generator(self) -> None:
        """Generates dynamic synthetic electronic music with rhythmic kick drums, bass, and pads."""
        logger.info("Running synthetic audio test generator (128 BPM electronic beat)")
        # 128 BPM -> 2.133 beats per second (~468.75ms per beat)
        beat_interval = 60.0 / 128.0
        phase = 0.0
        t_global = 0.0
        frame_sec = self.frame_duration_ms / 1000.0

        while self._running and self.mode == "test":
            start_time = time.perf_counter()
            frame_samples = []

            for i in range(self.samples_per_frame):
                t = t_global + (i / self.sample_rate)
                # Beat phase: 0.0 at kick start, 1.0 at next beat
                beat_phase = (t % beat_interval) / beat_interval

                # Kick drum (decaying pitch and amplitude in sub-bass)
                if beat_phase < 0.35:
                    kick_decay = math.exp(-beat_phase * 14.0)
                    kick_freq = 45.0 + (120.0 * kick_decay)
                    kick = math.sin(2.0 * math.pi * kick_freq * t) * kick_decay * 0.8
                else:
                    kick = 0.0

                # Offbeat bass (approx 110 Hz)
                if 0.45 < beat_phase < 0.90:
                    bass_decay = math.sin((beat_phase - 0.45) / 0.45 * math.pi)
                    bass = math.sin(2.0 * math.pi * 110.0 * t) * bass_decay * 0.4
                else:
                    bass = 0.0

                # Synth pad / chord in mid frequencies
                pad = (
                    math.sin(2.0 * math.pi * 330.0 * t) * 0.15 +
                    math.sin(2.0 * math.pi * 440.0 * t) * 0.15 +
                    math.sin(2.0 * math.pi * 554.3 * t) * 0.12
                )

                # Hi-hat (short bursts on off-beats)
                if 0.48 < beat_phase < 0.58:
                    noise = (hash(str(i + int(t * 1000))) % 1000 / 500.0 - 1.0)
                    hihat = noise * 0.25
                else:
                    hihat = 0.0

                val = int((kick + bass + pad + hihat) * 28000)
                val = max(-32768, min(32767, val))
                frame_samples.append(val)

            t_global += frame_sec

            # Interleave into stereo 16-bit PCM bytes
            raw_bytes = bytearray(self.bytes_per_frame)
            for idx, sample in enumerate(frame_samples):
                b = sample.to_bytes(2, byteorder="little", signed=True)
                raw_bytes[idx * 4: idx * 4 + 2] = b
                raw_bytes[idx * 4 + 2: idx * 4 + 4] = b

            if self._callback:
                self._callback(bytes(raw_bytes))

            elapsed = time.perf_counter() - start_time
            sleep_time = max(0.0, frame_sec - elapsed)
            time.sleep(sleep_time)

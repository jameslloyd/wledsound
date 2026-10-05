"""Configuration management for wledsound."""

import os
import yaml
import logging
from typing import List, Optional, Tuple, Dict, Any
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AudioSettings(BaseModel):
    mode: str = Field(default="squeezelite", description="Audio capture mode: 'squeezelite', 'snapclient', 'fifo', or 'test'")
    squeezelite_host: str = Field(default="127.0.0.1", description="Squeezelite / Slimproto server host")
    squeezelite_port: int = Field(default=3483, description="Squeezelite / Slimproto server port")
    squeezelite_player_name: str = Field(default="WLEDSound", description="Player name registered in Music Assistant")
    squeezelite_mac: str = Field(default="de:47:2d:67:f3:af", description="MAC address for Squeezelite player")
    auto_group: bool = Field(default=True, description="Automatically sync/group WLEDSound with active playing player")
    snapserver_host: str = Field(default="127.0.0.1", description="Snapserver hostname or IP")
    snapserver_port: int = Field(default=1704, description="Snapserver stream port")
    fifo_path: str = Field(default="/tmp/snapfifo", description="Named pipe path if using FIFO mode")
    sample_rate: int = Field(default=44100, description="PCM audio sample rate in Hz")
    sync_offset_ms: int = Field(default=0, description="Audio/LED beat synchronization timing offset in milliseconds (-250 to +1000ms)")
    gain: float = Field(default=1.0, description="Master audio gain multiplier (0.1 - 5.0)")
    squelch: float = Field(default=0.005, description="Silence/noise floor threshold (0.0 - 0.1)")
    agc_enabled: bool = Field(default=True, description="Automatic Gain Control")
    smoothing: float = Field(default=0.25, description="Audio level smoothing factor (0.05 - 0.9)")


class WLEDSegmentConfig(BaseModel):
    id: int = Field(default=0, description="WLED Segment ID")
    name: str = Field(default="Segment", description="Segment name (e.g. Top Cabinet)")
    start: int = Field(default=0, description="Start LED index (0-indexed)")
    stop: int = Field(default=60, description="Stop LED index (exclusive)")
    effect: str = Field(
        default="album_pulse",
        description="Effect for this segment: album_pulse, geq_spectrum, energy_wave, vu_meter, beat_flash, solid, off"
    )
    reverse: bool = Field(default=False, description="Reverse effect animation direction")
    mirror: bool = Field(default=False, description="Mirror animation from segment center")
    brightness: float = Field(default=1.0, description="Relative segment brightness multiplier (0.0 - 1.0)")
    palette: Optional[str] = Field(default=None, description="Optional custom palette override, or None to inherit master")


class CustomPaletteConfig(BaseModel):
    id: str = Field(description="Unique palette identifier slug")
    name: str = Field(description="Display name")
    colors: List[Tuple[int, int, int]] = Field(description="List of (R, G, B) color tuples (0-255)")


class WLEDDeviceConfig(BaseModel):
    ip: str = Field(description="WLED device IP address")
    name: str = Field(default="", description="Device friendly name (e.g. Kitchen1)")
    led_count: int = Field(default=60, description="Total LED count on this device")
    ddp_enabled: bool = Field(default=True, description="Enable real-time DDP streaming to this device")
    segments: List[WLEDSegmentConfig] = Field(
        default_factory=list,
        description="Configured segments on this device"
    )


class WLEDSettings(BaseModel):
    sync_enabled: bool = Field(default=True, description="Master enable toggle for LED audio synchronization")
    mode: str = Field(default="hybrid", description="Output mode: 'hybrid', 'audiosync', 'ddp', or 'off'")
    audiosync_targets: List[str] = Field(
        default_factory=lambda: ["239.0.0.1"],
        description="IP addresses to send AudioReactive UDP packets to (multicast or unicast)"
    )
    audiosync_port: int = Field(default=11988, description="WLED AudioSync UDP port")
    protocol_version: int = Field(default=2, description="WLED AudioSync protocol: 2 (v0.14+) or 1 (legacy)")
    ddp_targets: List[str] = Field(
        default_factory=lambda: [],
        description="IP addresses of WLED devices for direct DDP pixel streaming"
    )
    ddp_port: int = Field(default=4048, description="WLED DDP UDP port")
    led_count: int = Field(default=60, description="Number of LEDs in strip for DDP streaming")
    ddp_effect: str = Field(
        default="album_pulse",
        description="DDP visualizer: 'album_pulse', 'geq_spectrum', 'energy_wave', 'vu_meter', 'beat_flash'"
    )
    wled_hosts: List[str] = Field(
        default_factory=lambda: [],
        description="WLED device IPs for HTTP JSON API control (power, segment color palettes)"
    )
    auto_power: bool = Field(default=True, description="Turn WLED on when music plays, turn off/idle when paused")
    sync_album_art_colors: bool = Field(default=True, description="Push album art palette to WLED segment colors")
    palette: str = Field(default="album_art", description="Color palette: 'album_art', 'cyberpunk', 'sunset', 'vaporwave', 'aurora', 'magma', 'forest', 'glacial', 'rainbow', 'candle'")
    custom_palettes: List[CustomPaletteConfig] = Field(
        default_factory=list,
        description="User-defined custom color palettes"
    )
    default_preset: Optional[int] = Field(
        default=None,
        description="WLED preset ID to activate when LED sync is disconnected/disabled (or None to restore pre-sync state)"
    )
    devices: List[WLEDDeviceConfig] = Field(
        default_factory=list,
        description="Per-device configurations with individual segment definitions and effects"
    )


class MusicAssistantSettings(BaseModel):
    enabled: bool = Field(default=True, description="Enable Music Assistant integration")
    server_url: str = Field(default="http://127.0.0.1:8095", description="Music Assistant server URL")
    player_id: Optional[str] = Field(default=None, description="Target MA player ID (auto-select if None)")
    token: Optional[str] = Field(default=None, description="Optional MA access token")


class WebSettings(BaseModel):
    enabled: bool = Field(default=True, description="Enable Web UI & API dashboard")
    host: str = Field(default="0.0.0.0", description="Web server bind host")
    port: int = Field(default=8080, description="Web server bind port")


class AppConfig(BaseModel):
    audio: AudioSettings = Field(default_factory=AudioSettings)
    wled: WLEDSettings = Field(default_factory=WLEDSettings)
    music_assistant: MusicAssistantSettings = Field(default_factory=MusicAssistantSettings)
    web: WebSettings = Field(default_factory=WebSettings)


def load_config(config_path: str = "config.yaml") -> AppConfig:
    """Loads configuration from YAML file or returns defaults."""
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            logger.info(f"Loaded configuration from {config_path}")
            return AppConfig(**data)
        except Exception as e:
            logger.error(f"Error reading {config_path}: {e}. Using defaults.")

    config = AppConfig()
    # Save default config template if missing
    try:
        save_config(config, config_path)
    except Exception:
        pass
    return config


def save_config(config: AppConfig, config_path: str = "config.yaml") -> None:
    """Saves AppConfig back to YAML file."""
    data = config.model_dump()
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)

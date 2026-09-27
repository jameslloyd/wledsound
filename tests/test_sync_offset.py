import time
import pytest
from unittest.mock import MagicMock, patch
from wledsound.config import AppConfig, AudioSettings
from wledsound.audio.capture import AudioCapture
from wledsound.main import ServiceCoordinator


def test_audio_settings_sync_offset_default():
    settings = AudioSettings()
    assert settings.sync_offset_ms == 0


def test_audio_settings_sync_offset_custom():
    settings = AudioSettings(sync_offset_ms=120)
    assert settings.sync_offset_ms == 120
    settings_neg = AudioSettings(sync_offset_ms=-50)
    assert settings_neg.sync_offset_ms == -50


def test_capture_sync_offset():
    capture = AudioCapture(
        mode="test",
        sample_rate=44100,
        frame_duration_ms=20,
        sync_offset_ms=80
    )
    assert capture.sync_offset_ms == 80
    capture.sync_offset_ms = -30
    assert capture.sync_offset_ms == -30


def test_coordinator_set_sync_offset():
    config = AppConfig()
    coordinator = ServiceCoordinator(config=config)
    
    # Test setting positive offset
    new_val = coordinator.set_sync_offset(150)
    assert new_val == 150
    assert coordinator.config.audio.sync_offset_ms == 150
    assert coordinator.capture.sync_offset_ms == 150

    # Test setting negative offset within bounds
    new_val = coordinator.set_sync_offset(-100)
    assert new_val == -100
    assert coordinator.config.audio.sync_offset_ms == -100

    # Test clamping bounds (-250 to 1000)
    new_val = coordinator.set_sync_offset(-500)
    assert new_val == -250
    new_val = coordinator.set_sync_offset(2000)
    assert new_val == 1000


def test_coordinator_offset_queue_buffering():
    config = AppConfig()
    config.audio.sync_offset_ms = 100  # 100ms delay
    config.wled.sync_enabled = True
    coordinator = ServiceCoordinator(config=config)

    dispatched = []
    coordinator._dispatch_audio_frame = lambda pcm: dispatched.append(pcm)

    chunk1 = b"\x00\x00" * 882 * 2

    # Feed chunk 1
    t0 = time.perf_counter()
    coordinator._on_audio_chunk(chunk1)

    # Immediately after feeding, chunk1 should be queued (target_delay is 0.1s)
    assert len(coordinator._audio_queue) == 1
    assert len(dispatched) == 0

    # Simulate elapsed time > 100ms by manually setting the queued timestamp back
    coordinator._audio_queue[0] = (t0 - 0.12, chunk1)

    # Feed next chunk: chunk1 should now be ready and popped
    chunk2 = b"\x10\x10" * 882 * 2
    coordinator._on_audio_chunk(chunk2)

    assert len(dispatched) >= 1
    assert dispatched[0] == chunk1


def test_coordinator_zero_offset_immediate_dispatch():
    config = AppConfig()
    config.audio.sync_offset_ms = 0  # 0ms delay (realtime)
    coordinator = ServiceCoordinator(config=config)

    dispatched = []
    coordinator._dispatch_audio_frame = lambda pcm: dispatched.append(pcm)

    chunk = b"\x00\x00" * 882 * 2
    coordinator._on_audio_chunk(chunk)

    # At 0ms, frame should dispatch immediately
    assert len(dispatched) == 1
    assert dispatched[0] == chunk
    assert len(coordinator._audio_queue) == 0


@pytest.mark.anyio
async def test_api_audio_offset_endpoint(tmp_path):
    from unittest.mock import AsyncMock
    from wledsound.web.app import create_web_app

    cfg_file = str(tmp_path / "config.yaml")
    coordinator = ServiceCoordinator(config_path=cfg_file)
    app = create_web_app(coordinator)

    mock_request = AsyncMock()
    mock_request.json.return_value = {"offset_ms": 180}

    offset_route = next(r for r in app.routes if getattr(r, "path", None) == "/api/audio/offset")
    response = await offset_route.endpoint(mock_request)

    assert response["status"] == "ok"
    assert response["sync_offset_ms"] == 180
    assert coordinator.config.audio.sync_offset_ms == 180

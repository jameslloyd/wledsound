import pytest
from unittest.mock import MagicMock
from wledsound.config import AppConfig, WLEDSettings
from wledsound.main import ServiceCoordinator
from wledsound.web.app import create_web_app


def test_config_sync_enabled_default():
    config = AppConfig()
    assert config.wled.sync_enabled is True
    assert config.wled.mode == "hybrid"


def test_coordinator_toggle_sync(tmp_path):
    cfg_file = str(tmp_path / "config.yaml")
    coordinator = ServiceCoordinator(config_path=cfg_file)
    assert coordinator.config.wled.sync_enabled is True

    # Toggle to False
    res = coordinator.toggle_sync()
    assert res is False
    assert coordinator.config.wled.sync_enabled is False

    # Toggle to True
    res = coordinator.toggle_sync()
    assert res is True
    assert coordinator.config.wled.sync_enabled is True

    # Explicit set to False
    res = coordinator.toggle_sync(False)
    assert res is False
    assert coordinator.config.wled.sync_enabled is False


def test_telemetry_and_status_include_sync_enabled(tmp_path):
    cfg_file = str(tmp_path / "config.yaml")
    coordinator = ServiceCoordinator(config_path=cfg_file)

    frame = coordinator.get_telemetry_frame()
    assert "sync_enabled" in frame
    assert frame["sync_enabled"] is True

    status = coordinator.get_status()
    assert status["wled"]["sync_enabled"] is True

    coordinator.toggle_sync(False)
    assert coordinator.get_telemetry_frame()["sync_enabled"] is False
    assert coordinator.get_status()["wled"]["sync_enabled"] is False


def test_audio_chunk_bypasses_wled_when_sync_disabled(tmp_path):
    cfg_file = str(tmp_path / "config.yaml")
    coordinator = ServiceCoordinator(config_path=cfg_file)

    # Mock audiosync and ddp
    coordinator.audiosync.send_features = MagicMock()
    coordinator.ddp.send_frame = MagicMock()

    # Generate dummy stereo 16-bit PCM chunk (3840 bytes)
    dummy_chunk = b"\x00" * 3840

    # With sync_enabled=True and mode="hybrid": packets should be sent
    coordinator.config.wled.sync_enabled = True
    coordinator.config.wled.mode = "hybrid"
    coordinator._on_audio_chunk(dummy_chunk)
    assert coordinator.audiosync.send_features.called

    # Reset mocks
    coordinator.audiosync.send_features.reset_mock()
    coordinator.ddp.send_frame.reset_mock()

    # With sync_enabled=False: no packets should be sent
    coordinator.config.wled.sync_enabled = False
    coordinator._on_audio_chunk(dummy_chunk)
    assert not coordinator.audiosync.send_features.called
    assert not coordinator.ddp.send_frame.called

    # With sync_enabled=True but mode="off": no packets should be sent
    coordinator.config.wled.sync_enabled = True
    coordinator.config.wled.mode = "off"
    coordinator._on_audio_chunk(dummy_chunk)
    assert not coordinator.audiosync.send_features.called
    assert not coordinator.ddp.send_frame.called


@pytest.mark.anyio
async def test_api_sync_toggle_endpoint(tmp_path):
    from unittest.mock import AsyncMock

    cfg_file = str(tmp_path / "config.yaml")
    coordinator = ServiceCoordinator(config_path=cfg_file)
    app = create_web_app(coordinator)

    # Initially True
    assert coordinator.config.wled.sync_enabled is True

    # Test coordinator toggle_sync
    res = coordinator.toggle_sync()
    assert res is False
    assert coordinator.config.wled.sync_enabled is False

    res = coordinator.toggle_sync(True)
    assert res is True
    assert coordinator.config.wled.sync_enabled is True

    # Test endpoint logic with mock request
    mock_request = AsyncMock()
    mock_request.json.return_value = {"enabled": False}

    # Find the toggle_sync route handler
    sync_route = next(r for r in app.routes if getattr(r, "path", None) == "/api/wled/sync-toggle")
    response = await sync_route.endpoint(mock_request)
    assert response["status"] == "ok"
    assert response["sync_enabled"] is False
    assert coordinator.config.wled.sync_enabled is False

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from wledsound.config import AppConfig, CustomPaletteConfig
from wledsound.main import ServiceCoordinator
from wledsound.wled.client import WLEDClient
from wledsound.wled.effects import VisualizerEngine, get_palette_definitions
from wledsound.web.app import create_web_app


def test_visualizer_engine_custom_palette():
    engine = VisualizerEngine(led_count=60, palette_name="cyberpunk")
    assert "cyberpunk" in [p["id"] for p in engine.get_palette_definitions()]

    # Add custom palette
    engine.add_custom_palette(
        palette_id="acid_trance",
        name="Acid Trance",
        colors=[(50, 255, 0), (255, 0, 200), (0, 255, 255)]
    )

    palettes = engine.get_palette_definitions()
    custom_p = next((p for p in palettes if p["id"] == "acid_trance"), None)
    assert custom_p is not None
    assert custom_p["name"] == "Acid Trance"
    assert custom_p["is_custom"] is True
    assert custom_p["hex_colors"] == ["#32ff00", "#ff00c8", "#00ffff"]

    # Resolution by name
    resolved = engine.get_palette_by_name("acid_trance")
    assert resolved == [(50, 255, 0), (255, 0, 200), (0, 255, 255)]

    # Removal
    engine.remove_custom_palette("acid_trance")
    palettes_after = engine.get_palette_definitions()
    assert not any(p["id"] == "acid_trance" for p in palettes_after)


def test_coordinator_custom_palette_crud(tmp_path):
    cfg_file = str(tmp_path / "config.yaml")
    coordinator = ServiceCoordinator(config_path=cfg_file)

    # Save a custom palette with hex colors
    created = coordinator.save_custom_palette(
        name="Miami Sunset",
        colors=["#ff0055", "#00ffff", "#ffcc00"]
    )
    assert created["id"] == "custom_miami_sunset"
    assert created["name"] == "Miami Sunset"
    assert created["is_custom"] is True
    assert len(coordinator.config.wled.custom_palettes) == 1
    assert coordinator.config.wled.custom_palettes[0].id == "custom_miami_sunset"

    # Select custom palette
    coordinator.set_palette("custom_miami_sunset")
    assert coordinator.config.wled.palette == "custom_miami_sunset"
    assert coordinator.visualizer.palette_name == "custom_miami_sunset"

    # Delete custom palette (should revert master palette to album_art)
    deleted = coordinator.delete_custom_palette("custom_miami_sunset")
    assert deleted is True
    assert len(coordinator.config.wled.custom_palettes) == 0
    assert coordinator.config.wled.palette == "album_art"


@pytest.mark.anyio
async def test_wled_client_restore_default_state():
    client = WLEDClient("192.168.1.50")
    client._post_state = AsyncMock(return_value=True)

    # 1. Without saved state and without default_preset: releases live mode
    await client.restore_default_state()
    client._post_state.assert_called_with({"live": False, "lor": 0})

    # 2. With configured default_preset: loads preset and releases live mode
    await client.restore_default_state(default_preset=3)
    client._post_state.assert_called_with({"live": False, "lor": 0, "ps": 3})

    # 3. With captured baseline state having preset 2
    client._saved_state = {"ps": 2, "on": True, "bri": 200, "lor": 0}
    await client.restore_default_state()
    client._post_state.assert_called_with({"live": False, "lor": 0, "ps": 2})

    # 4. With captured baseline state having no preset (ps <= 0) but segment colors/bri
    client._saved_state = {
        "ps": -1,
        "on": True,
        "bri": 150,
        "seg": [{"id": 0, "col": [[255, 200, 100]], "fx": 0}]
    }
    await client.restore_default_state()
    client._post_state.assert_called_with({
        "live": False,
        "lor": 0,
        "on": True,
        "bri": 150,
        "seg": [{"id": 0, "col": [[255, 200, 100]], "fx": 0}]
    })


@pytest.mark.anyio
async def test_coordinator_disconnect_sync_restores_defaults(tmp_path):
    cfg_file = str(tmp_path / "config.yaml")
    coordinator = ServiceCoordinator(config_path=cfg_file)

    # Mock WLED client
    mock_client = AsyncMock(spec=WLEDClient)
    mock_client.restore_default_state = AsyncMock(return_value=True)
    coordinator.wled_clients = {"192.168.1.50": mock_client}

    # Calling restore_wled_defaults directly
    await coordinator.restore_wled_defaults()
    mock_client.restore_default_state.assert_called_once_with(default_preset=None)


@pytest.mark.anyio
async def test_api_custom_palettes_and_restore_defaults(tmp_path):
    cfg_file = str(tmp_path / "config.yaml")
    coordinator = ServiceCoordinator(config_path=cfg_file)
    mock_client = AsyncMock(spec=WLEDClient)
    mock_client.restore_default_state = AsyncMock(return_value=True)
    coordinator.wled_clients = {"192.168.1.50": mock_client}

    app = create_web_app(coordinator)

    # 1. GET palettes endpoint
    palettes_route = next(r for r in app.routes if getattr(r, "path", None) == "/api/wled/palettes")
    r_palettes = await palettes_route.endpoint()
    assert "palettes" in r_palettes
    assert any(p["id"] == "cyberpunk" for p in r_palettes["palettes"])

    # 2. POST custom palette endpoint
    create_route = next(r for r in app.routes if getattr(r, "path", None) == "/api/wled/palettes/custom")
    mock_req = AsyncMock()
    mock_req.json.return_value = {
        "name": "Neon Dreams",
        "colors": ["#112233", "#445566"]
    }
    r_create = await create_route.endpoint(mock_req)
    assert r_create["status"] == "ok"
    p_id = r_create["palette"]["id"]

    # 3. Verify palette is returned by GET palettes
    r_palettes2 = await palettes_route.endpoint()
    assert any(p["id"] == p_id for p in r_palettes2["palettes"])

    # 4. POST restore defaults endpoint
    restore_route = next(r for r in app.routes if getattr(r, "path", None) == "/api/wled/restore-defaults")
    r_restore = await restore_route.endpoint()
    assert r_restore["status"] == "ok"
    mock_client.restore_default_state.assert_called()

    # 5. DELETE custom palette endpoint
    del_route = next(r for r in app.routes if getattr(r, "path", None) == "/api/wled/palettes/custom/{palette_id}")
    r_del = await del_route.endpoint(palette_id=p_id)
    assert r_del["status"] == "ok"
    assert len(coordinator.config.wled.custom_palettes) == 0

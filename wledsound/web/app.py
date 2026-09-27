"""FastAPI web server and WebSocket broadcaster for WLEDSound dashboard."""

import os
import json
import asyncio
import logging
from typing import Set, Dict, Any, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


def create_web_app(coordinator: Any) -> FastAPI:
    """Creates the FastAPI application wired to the ServiceCoordinator."""
    app = FastAPI(title="WLEDSound", description="Music Assistant WLED Audio Reactive Sync")

    web_dir = os.path.dirname(os.path.abspath(__file__))
    static_dir = os.path.join(web_dir, "static")
    templates_dir = os.path.join(web_dir, "templates")

    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # Connected WebSocket clients
    active_websockets: Set[WebSocket] = set()

    @app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
    async def get_index():
        index_file = os.path.join(templates_dir, "index.html")
        if os.path.exists(index_file):
            with open(index_file, "r", encoding="utf-8") as f:
                return f.read()
        return "<h1>WLEDSound Dashboard</h1>"

    @app.get("/api/status")
    async def get_status():
        return coordinator.get_status()

    class ConfigPatch(BaseModel):
        audio: Optional[Dict[str, Any]] = None
        wled: Optional[Dict[str, Any]] = None
        music_assistant: Optional[Dict[str, Any]] = None

    @app.post("/api/config")
    async def update_config(patch: ConfigPatch):
        coordinator.apply_config_patch(patch.model_dump(exclude_none=True))
        return {"status": "ok", "config": coordinator.config.model_dump()}

    @app.post("/api/audio/offset")
    async def set_audio_offset(request: Request):
        try:
            payload = await request.json()
            offset_ms = int(payload.get("offset_ms", 0))
        except Exception:
            offset_ms = 0
        new_val = coordinator.set_sync_offset(offset_ms)
        return {"status": "ok", "sync_offset_ms": new_val}

    @app.post("/api/wled/power")
    async def toggle_power():
        success = await coordinator.toggle_wled_power()
        return {"status": "ok" if success else "error"}

    @app.post("/api/wled/sync-toggle")
    async def toggle_sync(request: Request):
        enabled = None
        try:
            body = await request.json()
            if isinstance(body, dict) and "enabled" in body:
                enabled = body["enabled"]
        except Exception:
            pass
        new_state = coordinator.toggle_sync(enabled)
        return {"status": "ok", "sync_enabled": new_state}

    @app.post("/api/wled/test-flash")
    async def test_flash():
        coordinator.trigger_test_flash()
        return {"status": "ok"}

    @app.post("/api/wled/sync-palette")
    async def sync_palette():
        success = await coordinator.sync_palette_to_wled()
        return {"status": "ok" if success else "error"}

    @app.post("/api/wled/palette/{palette_id}")
    async def set_palette(palette_id: str):
        coordinator.set_palette(palette_id)
        return {"status": "ok", "palette": palette_id}

    @app.get("/api/wled/devices")
    async def get_devices():
        return {"devices": coordinator.get_devices()}

    @app.post("/api/wled/devices/discover")
    async def discover_devices():
        devices = await coordinator.discover_devices()
        return {"status": "ok", "devices": devices}

    @app.post("/api/wled/devices/{ip}/segments/{segment_id}")
    async def update_device_segment(ip: str, segment_id: int, request: Request):
        payload = await request.json()
        success = coordinator.update_segment(ip, segment_id, payload)
        return {"status": "ok" if success else "error"}

    @app.post("/api/wled/devices")
    async def save_devices(request: Request):
        payload = await request.json()
        devices = payload.get("devices", [])
        coordinator.update_devices_config(devices)
        return {"status": "ok", "devices": coordinator.get_devices()}

    @app.websocket("/ws/visualizer")
    async def websocket_visualizer(websocket: WebSocket):
        await websocket.accept()
        active_websockets.add(websocket)
        logger.debug(f"Web visualizer client connected ({len(active_websockets)} total)")

        # Send initial state immediately upon connection
        try:
            initial_frame = coordinator.get_telemetry_frame()
            await websocket.send_text(json.dumps(initial_frame))
        except Exception:
            pass

        try:
            while True:
                # Keep connection alive & listen for client messages
                data = await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            active_websockets.discard(websocket)
            logger.debug(f"Web visualizer client disconnected ({len(active_websockets)} total)")

    async def broadcast_telemetry(frame_data: Dict[str, Any]):
        """Broadcasts live audio analysis frame to all connected browsers."""
        if not active_websockets:
            return
        msg = json.dumps(frame_data)
        dead_clients = set()
        for ws in active_websockets:
            try:
                await ws.send_text(msg)
            except Exception:
                dead_clients.add(ws)

        for dead in dead_clients:
            active_websockets.discard(dead)

    coordinator.set_telemetry_broadcaster(broadcast_telemetry)
    return app

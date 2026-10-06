"""Real Home Assistant objects and a mock Zaparoo protocol server."""

import json

import pytest
from aiohttp import ClientSession, web
from homeassistant.core import HomeAssistant


@pytest.fixture
async def hass(tmp_path):
    instance = HomeAssistant(str(tmp_path))
    instance.config.time_zone = "Europe/Zurich"
    yield instance
    await instance.async_stop()


@pytest.fixture
async def core_server():
    sockets = []
    requests = []
    snapshots = {
        "profiles": {
            "profiles": [
                {"profileId": "child", "name": "Child", "switchId": "secret-card"}
            ]
        },
        "profiles.active": {"profileId": "child", "name": "Child"},
        "playtime": {"dailyUsageToday": "12m30s", "dailyRemaining": "47m30s"},
        "media": {
            "active": [
                {"systemId": "TVShow", "launcherId": "Kodi", "mediaName": "Show"}
            ]
        },
        "clients.current": {"capabilities": ["profiles.manage"]},
        "readers": {"readers": []},
    }

    async def websocket(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        sockets.append(ws)
        async for message in ws:
            data = json.loads(message.data)
            requests.append(data)
            if data["method"] in snapshots.get("unsupported", []):
                await ws.send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": data["id"],
                        "error": {"code": -32601, "message": "Method not found"},
                    }
                )
            elif (
                data["method"] in snapshots.get("denied", [])
                or data["method"] == "fail"
            ):
                await ws.send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": data["id"],
                        "error": {"code": -32000, "message": "Denied"},
                    }
                )
            elif data["method"] == "drop":
                await ws.close()
            elif data["method"] == "profiles.update":
                profile = snapshots["profiles"]["profiles"][0]
                params = data["params"]
                if params.get("clearLimits"):
                    for key in ("dailyLimit", "sessionLimit", "limitsEnabled"):
                        profile.pop(key, None)
                profile.update({k: v for k, v in params.items() if k != "clearLimits"})
                await ws.send_json(
                    {"jsonrpc": "2.0", "id": data["id"], "result": profile}
                )
            else:
                await ws.send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": data["id"],
                        "result": snapshots.get(data["method"]),
                    }
                )
        return ws

    app = web.Application()
    app.router.add_get("/api/v0.1", websocket)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield port, sockets, requests, snapshots
    for ws in sockets:
        await ws.close()
    await runner.cleanup()


@pytest.fixture(autouse=True)
async def managed_http_session(monkeypatch):
    async with ClientSession() as session:
        monkeypatch.setattr(
            "custom_components.zaparoo.websocket_client.async_get_clientsession",
            lambda _: session,
        )
        yield session

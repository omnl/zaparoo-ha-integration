"""Run the compiled Go agent against the actual Python HA transport."""

import asyncio
import json
import os
import socket
from pathlib import Path

import aiohttp
import pytest

from custom_components.zaparoo.coordinator import ZaparooCoordinator
from custom_components.zaparoo.models import profile_allowance
from custom_components.zaparoo.websocket_client import ZaparooWebSocket

AGENT = os.environ.get("KIDSSTATION_AGENT_BINARY")
pytestmark = pytest.mark.skipif(
    not AGENT, reason="set KIDSSTATION_AGENT_BINARY to run the compiled agent"
)


def unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def test_ha_agent_core_and_persistent_restart(hass, core_server, tmp_path):
    core_port, _, requests, _ = core_server
    agent_port, hook_port = unused_port(), unused_port()
    token = "test-only-" + "x" * 64
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "listen": f"127.0.0.1:{agent_port}",
                "hookListen": f"127.0.0.1:{hook_port}",
                "token": token,
                "coreUrl": f"ws://127.0.0.1:{core_port}/api/v0.1",
                "stateFile": "state.json",
            }
        )
    )

    async def exercise(restarted=False):
        process = await asyncio.create_subprocess_exec(
            str(Path(AGENT).resolve()),
            "-config",
            str(config),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        coordinator = ZaparooCoordinator(hass)
        async with aiohttp.ClientSession() as session:
            client = ZaparooWebSocket(
                "127.0.0.1", agent_port, coordinator, session, token=token, agent=True
            )
            await client.start()
            try:
                await client.wait_ready(8)
                if not restarted:
                    await client.send_jsonrpc(
                        "kidsstation.policy.set",
                        {
                            "profileId": "child",
                            "timezone": "Europe/Zurich",
                            "enabled": True,
                            "minutes": [60] * 7,
                        },
                    )
                    for _ in range(2):
                        reply = await client.send_jsonrpc(
                            "kidsstation.add_time",
                            {
                                "profileId": "child",
                                "minutes": 30,
                                "requestId": "same-id-after-retry",
                            },
                        )
                    coordinator.handle_ws_event("kidsstation.state", reply["result"])
                assert profile_allowance(coordinator.data["policies"], "child") == (90, 30)
                assert coordinator.data["active_profile"]["profileId"] == "child"
                async with session.get(f"http://127.0.0.1:{hook_port}/check-launch") as response:
                    assert response.status == 200
                assert any(
                    r["method"] == "profiles.update" and r["params"].get("dailyLimit") == "1h30m0s"
                    for r in requests
                )
            finally:
                await client.stop()
                process.terminate()
                await asyncio.wait_for(process.wait(), 6)

    await exercise()
    await exercise(restarted=True)

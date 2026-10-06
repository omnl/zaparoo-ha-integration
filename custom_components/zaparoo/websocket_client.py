"""Persistent JSON-RPC transport with a complete reconnect snapshot."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid

import aiohttp
from homeassistant.exceptions import HomeAssistantError

from .const import API_PATH

_LOGGER = logging.getLogger(__name__)
SNAPSHOT_METHODS = (
    "profiles",
    "profiles.active",
    "playtime",
    "media",
    "clients.current",
    "readers",
)


class ZaparooWebSocket:
    """Connect to Core or through the local KidsStation agent."""

    def __init__(self, host, port, coordinator, session, *, token="", agent=False):
        self.host, self.port = host, port
        self.coordinator, self.session = coordinator, session
        self.token, self.agent = token, agent
        self._ws = self._task = None
        self._pending = {}
        self._ready = asyncio.Event()
        self._syncing = False
        self._buffer = []
        self._refresh_lock = asyncio.Lock()

    async def start(self):
        """Start once; setup waits until the snapshot is available."""
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def wait_ready(self, timeout=20):
        """Wait for synchronization rather than an open socket."""
        await asyncio.wait_for(self._ready.wait(), timeout)

    async def stop(self):
        """Cancel reconnect and every outstanding request."""
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._ws:
            await self._ws.close()
        self._ws = None
        self._fail_pending(HomeAssistantError("WebSocket stopped"))
        self._ready.clear()
        self.coordinator.disconnected()

    async def _run(self):
        delay = 1
        host = f"[{self.host}]" if ":" in self.host else self.host
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        while True:
            try:
                async with self.session.ws_connect(
                    f"ws://{host}:{self.port}{API_PATH}",
                    headers=headers,
                    heartbeat=20,
                    timeout=aiohttp.ClientWSTimeout(ws_close=5),
                ) as ws:
                    self._ws, self._syncing, self._buffer = ws, True, []
                    listener = asyncio.create_task(self._listen(ws))
                    try:
                        await self.refresh()
                        self._syncing = False
                        for method, params in self._buffer:
                            self.coordinator.handle_ws_event(method, params)
                        self._buffer = []
                        self.coordinator.connected()
                        self._ready.set()
                        delay = 1
                        while not listener.done():
                            done, _ = await asyncio.wait({listener}, timeout=15)
                            if done:
                                break
                            await self.refresh()
                        await listener
                    finally:
                        listener.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await listener
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, TimeoutError, HomeAssistantError, ValueError) as err:
                _LOGGER.debug("Zaparoo connection unavailable: %s", err)
            finally:
                self._ws, self._syncing = None, False
                self._ready.clear()
                self.coordinator.disconnected()
                self._fail_pending(HomeAssistantError("WebSocket disconnected"))
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def refresh(self):
        """Fetch state that notifications cannot replay after a disconnect."""
        async with self._refresh_lock:
            methods = (*SNAPSHOT_METHODS, "kidsstation.state") if self.agent else SNAPSHOT_METHODS
            replies = await asyncio.gather(*(self.send_jsonrpc(m) for m in methods))
            self.coordinator.set_snapshot(
                dict(zip(methods, (r["result"] for r in replies), strict=True))
            )

    async def _listen(self, ws):
        async for message in ws:
            if message.type == aiohttp.WSMsgType.TEXT:
                self._handle_message(message.data)
            elif message.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                break
        self._fail_pending(HomeAssistantError("WebSocket disconnected"))

    def _handle_message(self, message):
        try:
            data = json.loads(message)
        except (ValueError, TypeError):
            _LOGGER.debug("Ignoring invalid JSON-RPC message")
            return
        if not isinstance(data, dict):
            return
        if "id" in data:
            future = self._pending.get(data["id"])
            if future and not future.done():
                if "error" in data:
                    error = data["error"] or {}
                    future.set_exception(
                        HomeAssistantError(
                            f"RPC {error.get('code')}: {error.get('message', 'request failed')}"
                        )
                    )
                elif "result" in data:
                    future.set_result(data)
                else:
                    future.set_exception(HomeAssistantError("Invalid JSON-RPC response"))
        elif isinstance(data.get("method"), str):
            if self._syncing:
                self._buffer.append((data["method"], data.get("params")))
                if len(self._buffer) > 1024:
                    self._fail_pending(HomeAssistantError("Too many events during synchronization"))
            else:
                self.coordinator.handle_ws_event(data["method"], data.get("params"))

    async def send_jsonrpc(self, method, params=None):
        """Return a successful envelope; raise protocol errors immediately."""
        if self._ws is None or self._ws.closed:
            raise HomeAssistantError("Zaparoo WebSocket is not connected")
        rpc_id = str(uuid.uuid4())
        payload = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
        if params is not None:
            payload["params"] = params
        future = asyncio.get_running_loop().create_future()
        self._pending[rpc_id] = future
        try:
            await self._ws.send_json(payload)
            return await asyncio.wait_for(future, timeout=10)
        except (aiohttp.ClientError, OSError) as err:
            raise HomeAssistantError("WebSocket disconnected while sending request") from err
        finally:
            self._pending.pop(rpc_id, None)
            if not future.done():
                future.cancel()

    def _fail_pending(self, error):
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

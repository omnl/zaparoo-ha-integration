"""Persistent JSON-RPC transport with a complete reconnect snapshot."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

if TYPE_CHECKING:
    from .coordinator import ZaparooCoordinator

API_PATH = "/api/v0.1"

_LOGGER = logging.getLogger(__name__)
MAX_BUFFERED_EVENTS = 1024
METHOD_NOT_FOUND = -32601
SNAPSHOT_METHODS = ("media", "readers")
OPTIONAL_SNAPSHOT_METHODS = (
    "profiles",
    "profiles.active",
    "playtime",
    "clients.current",
)


class ZaparooRPCError(HomeAssistantError):
    """Preserve the protocol code for optional Core capabilities."""

    def __init__(self, code: int | None, detail: str) -> None:
        """Initialize a Core JSON-RPC error."""
        super().__init__(f"RPC {code}: {detail}")
        self.code = code


class ZaparooWebSocket:
    """Manage Core requests and restore state after reconnect."""

    def __init__(self, host: str, port: int, coordinator: ZaparooCoordinator) -> None:
        """Initialize one Core connection."""
        self.host, self.port = host, port
        self.coordinator = coordinator
        self.session = async_get_clientsession(coordinator.hass)
        self._ws = self._task = None
        self._pending = {}
        self._ready = asyncio.Event()
        self._syncing = False
        self._buffer = []
        self._refresh_lock = asyncio.Lock()
        self._profile_sequence = 0

    async def start(self) -> None:
        """Start once; setup waits until the snapshot is available."""
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def wait_ready(self, wait_seconds: float = 20) -> None:
        """Wait for synchronization rather than an open socket."""
        await asyncio.wait_for(self._ready.wait(), wait_seconds)

    async def stop(self) -> None:
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

    async def _run(self) -> None:
        delay = 1
        host = f"[{self.host}]" if ":" in self.host else self.host
        while True:
            try:
                async with self.session.ws_connect(
                    f"ws://{host}:{self.port}{API_PATH}",
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
            except (
                aiohttp.ClientError,
                TimeoutError,
                HomeAssistantError,
                ValueError,
            ) as err:
                _LOGGER.debug("Zaparoo connection unavailable: %s", err)
            finally:
                self._ws, self._syncing = None, False
                self._ready.clear()
                self.coordinator.disconnected()
                self._fail_pending(HomeAssistantError("WebSocket disconnected"))
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def refresh(self) -> None:
        """Fetch state that notifications cannot replay after a disconnect."""
        async with self._refresh_lock:
            profile_sequence = self._profile_sequence
            methods = SNAPSHOT_METHODS + tuple(
                m for m in OPTIONAL_SNAPSHOT_METHODS if m != "playtime"
            )
            replies = await asyncio.gather(
                *(self._snapshot_request(m) for m in methods)
            )
            snapshot = dict(zip(methods, (r["result"] for r in replies), strict=True))
            # Bracket accounting with identity reads and watch switch events,
            # including switches away and back during one refresh.
            snapshot["playtime"] = (await self._snapshot_request("playtime"))["result"]
            active = (await self._snapshot_request("profiles.active"))["result"]
            before_id = (snapshot.get("profiles.active") or {}).get("profileId")
            after_id = (active or {}).get("profileId")
            if before_id != after_id or profile_sequence != self._profile_sequence:
                snapshot["playtime"] = {}
            snapshot["profiles.active"] = active
            self.coordinator.set_snapshot(snapshot)

    async def _snapshot_request(self, method: str) -> dict[str, Any]:
        try:
            return await self.send_jsonrpc(method)
        except ZaparooRPCError as err:
            if method in OPTIONAL_SNAPSHOT_METHODS and err.code == METHOD_NOT_FOUND:
                return {"result": None}
            raise

    async def _listen(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async for message in ws:
            if message.type == aiohttp.WSMsgType.TEXT:
                self._handle_message(message.data)
            elif message.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                break
        self._fail_pending(HomeAssistantError("WebSocket disconnected"))

    def _handle_message(self, message: str) -> None:
        try:
            data = json.loads(message)
        except (ValueError, TypeError):
            _LOGGER.debug("Ignoring invalid JSON-RPC message")
            return
        if not isinstance(data, dict):
            return
        if "id" in data:
            if not isinstance(data["id"], str):
                return
            future = self._pending.get(data["id"])
            if future and not future.done():
                if "error" in data:
                    error = data["error"] if isinstance(data["error"], dict) else {}
                    code = error.get("code")
                    detail = error.get("message", "request failed")
                    future.set_exception(ZaparooRPCError(code, detail))
                elif "result" in data:
                    future.set_result(data)
                else:
                    future.set_exception(
                        HomeAssistantError("Invalid JSON-RPC response")
                    )
        elif isinstance(data.get("method"), str):
            self._handle_notification(data["method"], data.get("params"))

    def _handle_notification(self, method: str, params: Any) -> None:
        """Track profile changes even while startup events are buffered."""
        if method == "profiles.active":
            self._profile_sequence += 1
        if self._syncing:
            self._buffer.append((method, params))
            if len(self._buffer) > MAX_BUFFERED_EVENTS:
                self._fail_pending(
                    HomeAssistantError("Too many events during synchronization")
                )
        else:
            self.coordinator.handle_ws_event(method, params)

    async def send_jsonrpc(self, method: str, params: Any = None) -> dict[str, Any]:
        """Return a successful envelope; raise protocol errors immediately."""
        if self._ws is None or self._ws.closed:
            msg = "Zaparoo WebSocket is not connected"
            raise HomeAssistantError(msg)
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
            msg = "WebSocket disconnected while sending request"
            raise HomeAssistantError(msg) from err
        finally:
            self._pending.pop(rpc_id, None)
            if not future.done():
                future.cancel()

    def _fail_pending(self, error: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

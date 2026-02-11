from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Callable, Awaitable

import websockets
from websockets.asyncio.server import Server, ServerConnection

from conversation import Conversation


class Hub:
    def __init__(self, conversation: Conversation, host: str = "0.0.0.0", port: int = 9600):
        self.conversation = conversation
        self.host = host
        self.port = port
        self._server: Server | None = None
        self._worker_senders: dict[str, Callable[[str], Awaitable[None]]] = {}
        self._tool_to_worker: dict[str, str] = {}
        self._pending: dict[str, asyncio.Future[str]] = {}
        self._worker_ready = asyncio.Event()
        self._worker_count = 0
        self._tool_schemas: list[dict] = []

    @property
    def worker_count(self) -> int:
        return self._worker_count

    async def start(self) -> None:
        self._server = await websockets.serve(self._handle_worker, self.host, self.port)
        print(f"Hub listening on ws://{self.host}:{self.port}")

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def wait_for_workers(self, n: int = 1) -> None:
        while self._worker_count < n:
            self._worker_ready.clear()
            await self._worker_ready.wait()

    def register_tools_on(self, conv: Conversation) -> None:
        for schema in self._tool_schemas:
            name = schema["name"]
            async def _handler(__name=name, **kwargs: Any) -> str:
                return await self._dispatch(__name, kwargs)
            conv.register_tool(schema, _handler)

    async def _handle_worker(self, ws: ServerConnection) -> None:
        worker_id = str(uuid.uuid4())[:8]
        self._worker_senders[worker_id] = ws.send
        print(f"Worker {worker_id} connected")

        try:
            async for raw in ws:
                self._process_message(worker_id, raw)
        except websockets.ConnectionClosed:
            pass
        finally:
            self._cleanup_worker(worker_id)

    async def aiohttp_worker_handler(self, request: Any) -> Any:
        from aiohttp import web

        ws = web.WebSocketResponse()
        await ws.prepare(request)

        worker_id = str(uuid.uuid4())[:8]
        self._worker_senders[worker_id] = ws.send_str
        print(f"Worker {worker_id} connected (aiohttp)")

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    self._process_message(worker_id, msg.data)
                elif msg.type in (web.WSMsgType.ERROR, web.WSMsgType.CLOSE):
                    break
        finally:
            self._cleanup_worker(worker_id)

        return ws

    def _process_message(self, worker_id: str, raw: str) -> None:
        msg = json.loads(raw)
        msg_type = msg.get("type")

        if msg_type == "register":
            self._register_tools(worker_id, msg["tools"])
            self._worker_count += 1
            self._worker_ready.set()
            print(f"Worker {worker_id} registered {len(msg['tools'])} tool(s)")

        elif msg_type == "tool_result":
            call_id = msg["call_id"]
            fut = self._pending.pop(call_id, None)
            if fut and not fut.done():
                fut.set_result(msg["content"])

    def _cleanup_worker(self, worker_id: str) -> None:
        self._worker_senders.pop(worker_id, None)
        tools_to_remove = [t for t, w in self._tool_to_worker.items() if w == worker_id]
        for t in tools_to_remove:
            self._tool_to_worker.pop(t, None)
            self.conversation.tool_handlers.pop(t, None)
            self.conversation.tools = [s for s in self.conversation.tools if s["name"] != t]
            self._tool_schemas = [s for s in self._tool_schemas if s["name"] != t]
        if tools_to_remove:
            self._worker_count -= 1
        print(f"Worker {worker_id} disconnected")

    def _register_tools(self, worker_id: str, tool_schemas: list[dict[str, Any]]) -> None:
        for schema in tool_schemas:
            name = schema["name"]
            self._tool_to_worker[name] = worker_id
            self._tool_schemas.append(schema)

            async def _remote_handler(__name=name, **kwargs: Any) -> str:
                return await self._dispatch(__name, kwargs)

            self.conversation.register_tool(schema, _remote_handler)

    async def _dispatch(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        worker_id = self._tool_to_worker.get(tool_name)
        if worker_id is None:
            return f"Error: no worker registered for tool '{tool_name}'"

        send = self._worker_senders.get(worker_id)
        if send is None:
            return f"Error: worker for tool '{tool_name}' is disconnected"

        call_id = str(uuid.uuid4())
        fut: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        self._pending[call_id] = fut

        await send(json.dumps({
            "type": "tool_call",
            "call_id": call_id,
            "name": tool_name,
            "input": tool_input,
        }))

        return await fut

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import websockets
from websockets.asyncio.server import Server, ServerConnection

from conversation import Conversation


class Hub:
    def __init__(self, conversation: Conversation, host: str = "0.0.0.0", port: int = 9600):
        self.conversation = conversation
        self.host = host
        self.port = port
        self._server: Server | None = None
        self._workers: dict[str, ServerConnection] = {}
        self._tool_to_worker: dict[str, str] = {}
        self._pending: dict[str, asyncio.Future[str]] = {}
        self._worker_ready = asyncio.Event()
        self._worker_count = 0

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

    async def _handle_worker(self, ws: ServerConnection) -> None:
        worker_id = str(uuid.uuid4())[:8]
        self._workers[worker_id] = ws
        print(f"Worker {worker_id} connected")

        try:
            async for raw in ws:
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
        except websockets.ConnectionClosed:
            pass
        finally:
            self._workers.pop(worker_id, None)
            tools_to_remove = [t for t, w in self._tool_to_worker.items() if w == worker_id]
            for t in tools_to_remove:
                self._tool_to_worker.pop(t, None)
                self.conversation.tool_handlers.pop(t, None)
                self.conversation.tools = [s for s in self.conversation.tools if s["name"] != t]
            if tools_to_remove:
                self._worker_count -= 1
            print(f"Worker {worker_id} disconnected")

    def _register_tools(self, worker_id: str, tool_schemas: list[dict[str, Any]]) -> None:
        for schema in tool_schemas:
            name = schema["name"]
            self._tool_to_worker[name] = worker_id

            async def _remote_handler(__name=name, **kwargs: Any) -> str:
                return await self._dispatch(__name, kwargs)

            self.conversation.register_tool(schema, _remote_handler)

    async def _dispatch(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        worker_id = self._tool_to_worker.get(tool_name)
        if worker_id is None:
            return f"Error: no worker registered for tool '{tool_name}'"

        ws = self._workers.get(worker_id)
        if ws is None:
            return f"Error: worker for tool '{tool_name}' is disconnected"

        call_id = str(uuid.uuid4())
        fut: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        self._pending[call_id] = fut

        await ws.send(json.dumps({
            "type": "tool_call",
            "call_id": call_id,
            "name": tool_name,
            "input": tool_input,
        }))

        return await fut

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
        self._tool_to_workers: dict[str, list[str]] = {}
        self._tool_rr_index: dict[str, int] = {}
        self._session_affinity: dict[str, str] = {}
        self._pending: dict[str, asyncio.Future[str]] = {}
        self._busy_workers: set[str] = set()
        self._call_to_worker: dict[str, str] = {}
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

    def get_workers_info(self) -> list[dict[str, Any]]:
        workers: dict[str, list[str]] = {}
        for tool_name, worker_ids in self._tool_to_workers.items():
            for wid in worker_ids:
                if wid in self._worker_senders:
                    workers.setdefault(wid, []).append(tool_name)

        affinity_reverse: dict[str, list[str]] = {}
        for sid, wid in self._session_affinity.items():
            affinity_reverse.setdefault(wid, []).append(sid)

        return [
            {
                "worker_id": wid,
                "tools": tools,
                "status": "busy" if wid in self._busy_workers else "idle",
                "sessions": affinity_reverse.get(wid, []),
            }
            for wid, tools in workers.items()
        ]

    def register_tools_on(self, conv: Conversation, session_id: str | None = None) -> None:
        for schema in self._tool_schemas:
            name = schema["name"]
            async def _handler(__name=name, __sid=session_id, **kwargs: Any) -> str:
                return await self._dispatch(__name, kwargs, session_id=__sid)
            conv.register_tool(schema, _handler)

    async def _handle_worker(self, ws: ServerConnection) -> None:
        first_raw = await ws.recv()
        first_msg = json.loads(first_raw)
        worker_id = first_msg.get("worker_id") or str(uuid.uuid4())[:8]
        self._worker_senders[worker_id] = ws.send
        print(f"Worker {worker_id} connected")
        self._process_message(worker_id, first_raw)

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

        first_msg_raw = await ws.receive()
        if first_msg_raw.type != web.WSMsgType.TEXT:
            await ws.close()
            return ws
        first_msg = json.loads(first_msg_raw.data)
        worker_id = first_msg.get("worker_id") or str(uuid.uuid4())[:8]
        self._worker_senders[worker_id] = ws.send_str
        print(f"Worker {worker_id} connected (aiohttp)")
        self._process_message(worker_id, first_msg_raw.data)

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
            finished_wid = self._call_to_worker.pop(call_id, None)
            if finished_wid and not any(w == finished_wid for w in self._call_to_worker.values()):
                self._busy_workers.discard(finished_wid)
            fut = self._pending.pop(call_id, None)
            if fut and not fut.done():
                fut.set_result(msg["content"])

    def _cleanup_worker(self, worker_id: str) -> None:
        self._worker_senders.pop(worker_id, None)

        empty_tools: list[str] = []
        for tool_name, workers in self._tool_to_workers.items():
            if worker_id in workers:
                workers.remove(worker_id)
                if not workers:
                    empty_tools.append(tool_name)

        for t in empty_tools:
            del self._tool_to_workers[t]
            self._tool_rr_index.pop(t, None)
            self.conversation.tool_handlers.pop(t, None)
            self.conversation.tools = [s for s in self.conversation.tools if s["name"] != t]
            self._tool_schemas = [s for s in self._tool_schemas if s["name"] != t]

        stale_sessions = [sid for sid, wid in self._session_affinity.items() if wid == worker_id]
        for sid in stale_sessions:
            del self._session_affinity[sid]

        self._busy_workers.discard(worker_id)
        stale_calls = [cid for cid, wid in self._call_to_worker.items() if wid == worker_id]
        for cid in stale_calls:
            del self._call_to_worker[cid]

        self._worker_count -= 1
        print(f"Worker {worker_id} disconnected")

    def _register_tools(self, worker_id: str, tool_schemas: list[dict[str, Any]]) -> None:
        for schema in tool_schemas:
            name = schema["name"]
            workers = self._tool_to_workers.setdefault(name, [])
            if worker_id not in workers:
                workers.append(worker_id)

            if not any(s["name"] == name for s in self._tool_schemas):
                self._tool_schemas.append(schema)
                self._tool_rr_index[name] = 0

                async def _remote_handler(__name=name, **kwargs: Any) -> str:
                    return await self._dispatch(__name, kwargs)

                self.conversation.register_tool(schema, _remote_handler)

    def _pick_worker(self, tool_name: str, session_id: str | None) -> str | None:
        workers = self._tool_to_workers.get(tool_name)
        if not workers:
            return None

        if session_id:
            affinity_wid = self._session_affinity.get(session_id)
            if affinity_wid and affinity_wid in workers and affinity_wid in self._worker_senders:
                return affinity_wid

        alive = [w for w in workers if w in self._worker_senders]
        if not alive:
            return None

        idx = self._tool_rr_index.get(tool_name, 0) % len(alive)
        self._tool_rr_index[tool_name] = idx + 1
        chosen = alive[idx]

        if session_id:
            self._session_affinity[session_id] = chosen

        return chosen

    async def _dispatch(self, tool_name: str, tool_input: dict[str, Any], session_id: str | None = None) -> str:
        worker_id = self._pick_worker(tool_name, session_id)
        if worker_id is None:
            return f"Error: no worker registered for tool '{tool_name}'"

        send = self._worker_senders.get(worker_id)
        if send is None:
            return f"Error: worker for tool '{tool_name}' is disconnected"

        call_id = str(uuid.uuid4())
        fut: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        self._pending[call_id] = fut
        self._call_to_worker[call_id] = worker_id
        self._busy_workers.add(worker_id)

        await send(json.dumps({
            "type": "tool_call",
            "call_id": call_id,
            "name": tool_name,
            "input": tool_input,
        }))

        try:
            return await asyncio.wait_for(fut, timeout=120)
        except asyncio.TimeoutError:
            self._pending.pop(call_id, None)
            self._call_to_worker.pop(call_id, None)
            if not any(w == worker_id for w in self._call_to_worker.values()):
                self._busy_workers.discard(worker_id)
            return f"Error: tool '{tool_name}' timed out after 120s"

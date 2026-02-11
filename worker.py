#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import uuid

import websockets
from aiohttp import web

from tools import ALL_TOOLS

connected = False


async def healthz(request: web.Request) -> web.Response:
    if connected:
        return web.Response(text="ok")
    return web.Response(status=503, text="disconnected")


async def run_health_server(port: int) -> None:
    app = web.Application()
    app.router.add_get("/healthz", healthz)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Health server listening on 0.0.0.0:{port}")


async def run_worker(server_url: str, worker_id: str) -> None:
    global connected

    schemas = [schema for schema, _ in ALL_TOOLS]
    handlers = {schema["name"]: handler for schema, handler in ALL_TOOLS}

    while True:
        try:
            async with websockets.connect(server_url) as ws:
                await ws.send(json.dumps({"type": "register", "tools": schemas, "worker_id": worker_id}))
                connected = True
                print(f"Worker {worker_id} registered {len(schemas)} tool(s) with hub at {server_url}")

                async def handle_call(call_id: str, name: str, tool_input: dict, handler) -> None:
                    loop = asyncio.get_event_loop()
                    try:
                        if asyncio.iscoroutinefunction(handler):
                            result = await handler(**tool_input)
                        else:
                            result = await loop.run_in_executor(None, lambda: handler(**tool_input))
                    except Exception as e:
                        result = f"Error: {e}"
                    if not isinstance(result, str):
                        result = json.dumps(result)
                    try:
                        await ws.send(json.dumps({
                            "type": "tool_result",
                            "call_id": call_id,
                            "content": result,
                        }))
                    except Exception as e:
                        print(f"Failed to send result for {call_id}: {e}")
                        await ws.close()

                async for raw in ws:
                    msg = json.loads(raw)
                    if msg["type"] != "tool_call":
                        continue

                    call_id = msg["call_id"]
                    name = msg["name"]
                    tool_input = msg["input"]

                    handler = handlers.get(name)
                    if handler is None:
                        await ws.send(json.dumps({
                            "type": "tool_result",
                            "call_id": call_id,
                            "content": f"Error: unknown tool '{name}'",
                        }))
                    else:
                        asyncio.create_task(handle_call(call_id, name, tool_input, handler))

        except (ConnectionRefusedError, websockets.ConnectionClosed, OSError) as e:
            connected = False
            print(f"Connection lost ({e}), reconnecting in 2s...")
            await asyncio.sleep(2)


async def async_main(server_url: str, health_port: int, worker_id: str) -> None:
    await run_health_server(health_port)
    await run_worker(server_url, worker_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Tool worker -- connects to a conversation hub")
    parser.add_argument("--server", default="ws://localhost:9600", help="WebSocket URL of the hub")
    parser.add_argument("--health-port", type=int, default=8080, help="Port for the /healthz endpoint")
    parser.add_argument("--id", default=None, help="Worker ID (default: random)")
    args = parser.parse_args()

    worker_id = args.id or str(uuid.uuid4())[:8]
    print(f"Starting worker {worker_id}, connecting to {args.server}")
    asyncio.run(async_main(args.server, args.health_port, worker_id))


if __name__ == "__main__":
    main()

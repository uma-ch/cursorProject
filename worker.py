#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json

import websockets

from tools import ALL_TOOLS


async def run_worker(server_url: str) -> None:
    schemas = [schema for schema, _ in ALL_TOOLS]
    handlers = {schema["name"]: handler for schema, handler in ALL_TOOLS}

    while True:
        try:
            async with websockets.connect(server_url) as ws:
                await ws.send(json.dumps({"type": "register", "tools": schemas}))
                print(f"Registered {len(schemas)} tool(s) with hub at {server_url}")

                async for raw in ws:
                    msg = json.loads(raw)
                    if msg["type"] != "tool_call":
                        continue

                    call_id = msg["call_id"]
                    name = msg["name"]
                    tool_input = msg["input"]

                    handler = handlers.get(name)
                    if handler is None:
                        result = f"Error: unknown tool '{name}'"
                    else:
                        try:
                            result = handler(**tool_input)
                        except Exception as e:
                            result = f"Error: {e}"

                    if not isinstance(result, str):
                        result = json.dumps(result)

                    await ws.send(json.dumps({
                        "type": "tool_result",
                        "call_id": call_id,
                        "content": result,
                    }))

        except (ConnectionRefusedError, websockets.ConnectionClosed, OSError) as e:
            print(f"Connection lost ({e}), reconnecting in 2s...")
            await asyncio.sleep(2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Tool worker -- connects to a conversation hub")
    parser.add_argument("--server", default="ws://localhost:9600", help="WebSocket URL of the hub")
    args = parser.parse_args()

    print(f"Starting worker, connecting to {args.server}")
    asyncio.run(run_worker(args.server))


if __name__ == "__main__":
    main()

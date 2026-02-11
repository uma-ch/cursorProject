#!/usr/bin/env python3
from __future__ import annotations

import json
import os

from aiohttp import web

from conversation import Conversation
from hub import Hub

DEFAULT_MODEL = "claude-sonnet-4-20250514"
DEFAULT_MAX_TOKENS = 8192

hub: Hub | None = None


def get_hub() -> Hub:
    assert hub is not None, "Hub not initialized"
    return hub


async def healthz(request: web.Request) -> web.Response:
    h = get_hub()
    if h.worker_count > 0:
        return web.Response(text="ok")
    return web.Response(status=503, text="no workers connected")


async def prompt_handler(request: web.Request) -> web.Response:
    h = get_hub()
    if h.worker_count == 0:
        return web.Response(status=503, text="no workers connected")

    body = await request.json()
    prompt_text = body.get("prompt", "")
    if not prompt_text:
        return web.Response(status=400, text="missing 'prompt' field")

    model = body.get("model", DEFAULT_MODEL)
    system = body.get("system")
    max_tokens = body.get("max_tokens", DEFAULT_MAX_TOKENS)

    conv = Conversation(model=model, system=system, max_tokens=max_tokens)
    h.register_tools_on(conv)

    result = await conv.run_until_done(prompt_text)
    return web.json_response({"result": result})


async def ws_chat_handler(request: web.Request) -> web.WebSocketResponse:
    h = get_hub()
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    model = request.query.get("model", DEFAULT_MODEL)
    system = request.query.get("system")
    max_tokens = int(request.query.get("max_tokens", str(DEFAULT_MAX_TOKENS)))

    conv = Conversation(model=model, system=system, max_tokens=max_tokens)
    h.register_tools_on(conv)

    async for msg in ws:
        if msg.type == web.WSMsgType.TEXT:
            user_text = msg.data

            if h.worker_count == 0:
                await ws.send_str(json.dumps({"type": "error", "content": "no workers connected"}))
                continue

            response = await conv.send(user_text)

            while response.stop_reason == "tool_use":
                for block in response.content:
                    if block.type == "tool_use":
                        await ws.send_str(json.dumps({
                            "type": "tool_use",
                            "name": block.name,
                            "input": block.input,
                        }))
                await conv._handle_tool_use(response)
                response = await conv.step()

            text_parts = [b.text for b in response.content if b.type == "text"]
            await ws.send_str(json.dumps({
                "type": "done",
                "content": "\n".join(text_parts),
            }))

        elif msg.type in (web.WSMsgType.ERROR, web.WSMsgType.CLOSE):
            break

    return ws


def create_app() -> web.Application:
    global hub

    conv = Conversation()
    hub = Hub(conv)

    app = web.Application()
    app.router.add_get("/healthz", healthz)
    app.router.add_post("/prompt", prompt_handler)
    app.router.add_get("/ws/chat", ws_chat_handler)
    app.router.add_get("/ws/worker", hub.aiohttp_worker_handler)
    return app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app = create_app()
    print(f"Starting server on 0.0.0.0:{port}")
    web.run_app(app, host="0.0.0.0", port=port)

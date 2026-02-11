#!/usr/bin/env python3
from __future__ import annotations

import json
import os

from aiohttp import web

from conversation import Conversation
from hub import Hub
from sessions import SessionStore

from dotenv import load_dotenv
load_dotenv()

DEFAULT_MODEL = "claude-sonnet-4-20250514"
DEFAULT_MAX_TOKENS = 8192

hub: Hub | None = None
store: SessionStore | None = None


def get_hub() -> Hub:
    assert hub is not None, "Hub not initialized"
    return hub


def get_store() -> SessionStore:
    assert store is not None, "SessionStore not initialized"
    return store


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


async def index_redirect(request: web.Request) -> web.HTTPFound:
    raise web.HTTPFound("/static/index.html")


async def workers_handler(request: web.Request) -> web.Response:
    h = get_hub()
    return web.json_response(h.get_workers_info())


# --- Session routes ---

async def create_session(request: web.Request) -> web.Response:
    s = get_store()
    body = await request.json() if request.content_length else {}
    session_id = s.create(
        model=body.get("model", DEFAULT_MODEL),
        system=body.get("system"),
        max_tokens=body.get("max_tokens", DEFAULT_MAX_TOKENS),
    )
    return web.json_response({"session_id": session_id}, status=201)


async def list_sessions(request: web.Request) -> web.Response:
    s = get_store()
    return web.json_response(s.list_all())


async def get_session(request: web.Request) -> web.Response:
    s = get_store()
    session_id = request.match_info["id"]
    if not s.exists(session_id):
        return web.Response(status=404, text="session not found")
    return web.json_response(s.get(session_id))


async def delete_all_sessions(request: web.Request) -> web.Response:
    s = get_store()
    s.delete_all()
    return web.Response(status=204)


async def delete_session(request: web.Request) -> web.Response:
    s = get_store()
    session_id = request.match_info["id"]
    if not s.exists(session_id):
        return web.Response(status=404, text="session not found")
    s.delete(session_id)
    return web.Response(status=204)


async def session_prompt_handler(request: web.Request) -> web.Response:
    h = get_hub()
    s = get_store()
    session_id = request.match_info["id"]

    if not s.exists(session_id):
        return web.Response(status=404, text="session not found")
    if h.worker_count == 0:
        return web.Response(status=503, text="no workers connected")

    body = await request.json()
    prompt_text = body.get("prompt", "")
    if not prompt_text:
        return web.Response(status=400, text="missing 'prompt' field")

    conv = s.load(session_id)
    h.register_tools_on(conv)

    result = await conv.run_until_done(prompt_text)
    s.save(session_id, conv)

    return web.json_response({"result": result})


async def session_chat_handler(request: web.Request) -> web.WebSocketResponse:
    h = get_hub()
    s = get_store()
    session_id = request.match_info["id"]

    ws = web.WebSocketResponse()
    await ws.prepare(request)

    if not s.exists(session_id):
        await ws.send_str(json.dumps({"type": "error", "content": "session not found"}))
        await ws.close()
        return ws

    conv = s.load(session_id)
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

            s.save(session_id, conv)

        elif msg.type in (web.WSMsgType.ERROR, web.WSMsgType.CLOSE):
            break

    return ws


def create_app() -> web.Application:
    global hub, store

    conv = Conversation()
    hub = Hub(conv)
    store = SessionStore()

    static_dir = os.path.join(os.path.dirname(__file__), "static")

    app = web.Application()
    app.router.add_get("/", index_redirect)
    app.router.add_get("/healthz", healthz)
    app.router.add_post("/prompt", prompt_handler)
    app.router.add_get("/ws/chat", ws_chat_handler)
    app.router.add_get("/ws/worker", hub.aiohttp_worker_handler)
    app.router.add_get("/api/workers", workers_handler)

    app.router.add_post("/sessions", create_session)
    app.router.add_get("/sessions", list_sessions)
    app.router.add_delete("/sessions", delete_all_sessions)
    app.router.add_get("/sessions/{id}", get_session)
    app.router.add_delete("/sessions/{id}", delete_session)
    app.router.add_post("/sessions/{id}/prompt", session_prompt_handler)
    app.router.add_get("/sessions/{id}/chat", session_chat_handler)

    app.router.add_static("/static", static_dir)

    return app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app = create_app()
    print(f"Starting server on 0.0.0.0:{port}")
    web.run_app(app, host="0.0.0.0", port=port)

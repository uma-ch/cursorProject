#!/usr/bin/env python3
from __future__ import annotations

import asyncio
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


def _parse_ws_message(raw: str) -> tuple[str, str | None]:
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            if data.get("type") == "cancel":
                return "cancel", None
            if data.get("type") == "message":
                return "message", data.get("content", "")
    except (json.JSONDecodeError, TypeError):
        pass
    return "message", raw


async def run_agent_loop(
    conv: Conversation,
    ws: web.WebSocketResponse,
    user_text: str,
    store: SessionStore | None = None,
    session_id: str | None = None,
) -> None:
    try:
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

            results = conv.messages[-1]["content"] if conv.messages else []
            for r in results:
                if isinstance(r, dict) and r.get("type") == "tool_result":
                    await ws.send_str(json.dumps({
                        "type": "tool_result",
                        "tool_use_id": r["tool_use_id"],
                        "content": r.get("content", ""),
                    }))

            response = await conv.step()

        text_parts = [b.text for b in response.content if b.type == "text"]
        await ws.send_str(json.dumps({
            "type": "done",
            "content": "\n".join(text_parts),
        }))

        if store and session_id:
            store.save(session_id, conv)

    except asyncio.CancelledError:
        try:
            await ws.send_str(json.dumps({"type": "cancelled"}))
        except Exception:
            pass
        if store and session_id:
            store.save(session_id, conv)


async def ws_chat_handler(request: web.Request) -> web.WebSocketResponse:
    h = get_hub()
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    model = request.query.get("model", DEFAULT_MODEL)
    system = request.query.get("system")
    max_tokens = int(request.query.get("max_tokens", str(DEFAULT_MAX_TOKENS)))

    conv = Conversation(model=model, system=system, max_tokens=max_tokens)
    h.register_tools_on(conv)

    current_task: asyncio.Task | None = None

    async def _cancel_current() -> None:
        nonlocal current_task
        if current_task and not current_task.done():
            current_task.cancel()
            try:
                await current_task
            except (asyncio.CancelledError, Exception):
                pass
            current_task = None

    async for msg in ws:
        if msg.type == web.WSMsgType.TEXT:
            kind, content = _parse_ws_message(msg.data)
            if kind == "cancel":
                await _cancel_current()
            elif kind == "message" and content:
                await _cancel_current()
                current_task = asyncio.create_task(
                    run_agent_loop(conv, ws, content)
                )
        elif msg.type in (web.WSMsgType.ERROR, web.WSMsgType.CLOSE):
            break

    await _cancel_current()

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


async def clear_session_history(request: web.Request) -> web.Response:
    s = get_store()
    session_id = request.match_info["id"]
    if not s.exists(session_id):
        return web.Response(status=404, text="session not found")
    s.clear_history(session_id)
    return web.Response(status=204)


async def clear_all_history(request: web.Request) -> web.Response:
    s = get_store()
    s.clear_all_history()
    return web.Response(status=204)


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

    body = await request.json()
    prompt_text = body.get("prompt", "")
    if not prompt_text:
        return web.Response(status=400, text="missing 'prompt' field")

    conv = s.load(session_id)
    h.register_tools_on(conv, session_id=session_id)

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
    h.register_tools_on(conv, session_id=session_id)

    current_task: asyncio.Task | None = None

    async def _cancel_current() -> None:
        nonlocal current_task
        if current_task and not current_task.done():
            current_task.cancel()
            try:
                await current_task
            except (asyncio.CancelledError, Exception):
                pass
            current_task = None

    async for msg in ws:
        if msg.type == web.WSMsgType.TEXT:
            kind, content = _parse_ws_message(msg.data)
            if kind == "cancel":
                await _cancel_current()
            elif kind == "message" and content:
                await _cancel_current()
                current_task = asyncio.create_task(
                    run_agent_loop(conv, ws, content, store=s, session_id=session_id)
                )
        elif msg.type in (web.WSMsgType.ERROR, web.WSMsgType.CLOSE):
            break

    await _cancel_current()

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
    app.router.add_post("/sessions/{id}/clear", clear_session_history)
    app.router.add_get("/sessions/{id}/chat", session_chat_handler)
    app.router.add_post("/sessions/clear-all-history", clear_all_history)

    app.router.add_static("/static", static_dir)

    return app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app = create_app()
    print(f"Starting server on 0.0.0.0:{port}")
    web.run_app(app, host="0.0.0.0", port=port)

#!/usr/bin/env python3
"""Automated demo/test scenarios for the agent hub.

Assumes ./start.sh is already running with at least 3 workers.
Run: python test_demos.py
"""
from __future__ import annotations

import asyncio
import json
import time

import aiohttp

BASE = "http://localhost:8080"
MANAGER = "http://localhost:9090"
WS_BASE = "ws://localhost:8080"


async def create_session() -> str:
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{BASE}/sessions") as r:
            data = await r.json()
            return data["session_id"]


async def prompt_session(session_id: str, prompt: str) -> str:
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"{BASE}/sessions/{session_id}/prompt",
            json={"prompt": prompt},
            timeout=aiohttp.ClientTimeout(total=180),
        ) as r:
            data = await r.json()
            return data["result"]


async def delete_session(session_id: str) -> None:
    async with aiohttp.ClientSession() as s:
        await s.delete(f"{BASE}/sessions/{session_id}")


async def test_basic_agent_chat() -> None:
    sid = await create_session()
    try:
        result = await prompt_session(sid, "List the files in the current directory")
        assert "server.py" in result or "worker.py" in result, f"Expected filenames in response, got: {result[:200]}"
    finally:
        await delete_session(sid)


async def test_multi_tool_chain() -> None:
    sid = await create_session()
    try:
        result = await prompt_session(
            sid,
            "Read the file README.md and tell me what it says, then run 'wc -l *.py' and show me the output",
        )
        assert "wc" in result.lower() or any(c.isdigit() for c in result), f"Expected line counts in response, got: {result[:200]}"
    finally:
        await delete_session(sid)


async def test_long_running_cancel() -> None:
    sid = await create_session()
    try:
        async with aiohttp.ClientSession() as s:
            ws = await s.ws_connect(f"{WS_BASE}/sessions/{sid}/chat")
            await ws.send_str(json.dumps({"type": "message", "content": "Run the command 'sleep 60 && echo done'"}))

            await asyncio.sleep(3)
            await ws.send_str(json.dumps({"type": "cancel"}))

            got_cancelled = False
            deadline = time.time() + 10
            while time.time() < deadline:
                msg = await asyncio.wait_for(ws.receive(), timeout=5)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get("type") == "cancelled":
                        got_cancelled = True
                        break
                    if data.get("type") == "done":
                        break
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break

            assert got_cancelled, "Expected 'cancelled' message but didn't receive one"
            await ws.close()
    finally:
        await delete_session(sid)


async def test_concurrent_agents() -> None:
    sids = [await create_session() for _ in range(3)]
    try:
        prompts = [
            "What is 2 + 2?",
            "What is the capital of France?",
            "List files in the current directory",
        ]
        tasks = [prompt_session(sid, p) for sid, p in zip(sids, prompts)]
        results = await asyncio.gather(*tasks)
        for i, result in enumerate(results):
            assert len(result) > 0, f"Session {i} returned empty result"
    finally:
        for sid in sids:
            await delete_session(sid)


async def test_worker_resilience() -> None:
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{MANAGER}/api/workers") as r:
            workers = await r.json()
        assert len(workers) > 0, "No workers running"
        initial_count = len(workers)

        target_worker = workers[-1]
        wid = target_worker["id"]

        await s.delete(f"{MANAGER}/api/workers/{wid}")

        await asyncio.sleep(2)

        sid = await create_session()
        try:
            result = await prompt_session(sid, "What is 1 + 1?")
            assert len(result) > 0, "Got empty result after worker kill"
        finally:
            await delete_session(sid)

        await s.post(f"{MANAGER}/api/workers")
        await asyncio.sleep(2)

        async with s.get(f"{MANAGER}/api/workers") as r:
            workers_after = await r.json()
        assert len(workers_after) >= initial_count, f"Expected at least {initial_count} workers, got {len(workers_after)}"


async def test_worker_scaling() -> None:
    async with aiohttp.ClientSession() as s:
        await s.post(f"{MANAGER}/api/scale", json={"target": 1})
        await asyncio.sleep(3)

        sids = [await create_session() for _ in range(3)]
        try:
            start = time.time()
            tasks = [prompt_session(sid, "What is 1 + 1?") for sid in sids]
            await asyncio.gather(*tasks)
            time_with_1 = time.time() - start

            await s.post(f"{MANAGER}/api/scale", json={"target": 3})
            await asyncio.sleep(3)

            sids2 = [await create_session() for _ in range(3)]
            start = time.time()
            tasks = [prompt_session(sid, "What is 1 + 1?") for sid in sids2]
            await asyncio.gather(*tasks)
            time_with_3 = time.time() - start

            print(f"  1 worker: {time_with_1:.1f}s, 3 workers: {time_with_3:.1f}s")

            for sid in sids2:
                await delete_session(sid)
        finally:
            for sid in sids:
                await delete_session(sid)
            await s.post(f"{MANAGER}/api/scale", json={"target": 5})
            await asyncio.sleep(2)


async def test_session_persistence() -> None:
    sid = await create_session()
    try:
        await prompt_session(sid, "Hello, my name is TestBot")

        async with aiohttp.ClientSession() as s:
            async with s.get(f"{BASE}/sessions/{sid}") as r:
                data = await r.json()

        assert "messages" in data, "Session data missing messages"
        assert len(data["messages"]) > 0, "Session has no messages"

        user_msgs = [m for m in data["messages"] if m.get("role") == "user"]
        assert any("TestBot" in str(m) for m in user_msgs), "User message not persisted"

        async with aiohttp.ClientSession() as s:
            await s.post(f"{BASE}/sessions/{sid}/clear")
            async with s.get(f"{BASE}/sessions/{sid}") as r:
                data = await r.json()

        assert len(data.get("messages", [])) == 0, "Messages not cleared"
    finally:
        await delete_session(sid)


async def test_practical_use_case() -> None:
    sid = await create_session()
    try:
        result = await prompt_session(
            sid,
            "Write a Python script to /tmp/uma_test_fib.py that prints the first 10 Fibonacci numbers, then run it and show me the output",
        )
        assert any(str(n) in result for n in [1, 2, 3, 5, 8, 13, 21, 34]), f"Expected Fibonacci numbers in output, got: {result[:200]}"
    finally:
        await delete_session(sid)


async def main() -> None:
    tests = [
        test_basic_agent_chat,
        test_multi_tool_chain,
        test_long_running_cancel,
        test_concurrent_agents,
        test_worker_resilience,
        test_worker_scaling,
        test_session_persistence,
        test_practical_use_case,
    ]

    passed = 0
    failed = 0

    for t in tests:
        name = t.__name__
        print(f"Running {name}...")
        try:
            await t()
            print(f"  PASS: {name}")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {name}: {e}")
            failed += 1

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")
    print(f"{'='*40}")


if __name__ == "__main__":
    asyncio.run(main())

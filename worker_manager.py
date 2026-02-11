#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
from typing import Any

import aiohttp
from aiohttp import web

CONFIG_FILE = "worker_pool.json"
LOGS_DIR = "logs"


class PoolManager:
    def __init__(self) -> None:
        self._config: dict[str, Any] = {"hub_url": "", "base_port": 8081, "workers": []}
        self._load()

    def _load(self) -> None:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                self._config = json.load(f)

    def _save(self) -> None:
        with open(CONFIG_FILE, "w") as f:
            json.dump(self._config, f, indent=2)

    @property
    def hub_url(self) -> str:
        return self._config.get("hub_url", "")

    @property
    def base_port(self) -> int:
        return self._config.get("base_port", 8081)

    @property
    def workers(self) -> list[dict[str, Any]]:
        return self._config.get("workers", [])

    def set_config(self, hub_url: str, base_port: int) -> None:
        self._config["hub_url"] = hub_url
        self._config["base_port"] = base_port
        self._save()

    def _next_worker_id(self) -> str:
        existing = {w["id"] for w in self.workers}
        n = 1
        while f"w{n}" in existing:
            n += 1
        return f"w{n}"

    @staticmethod
    def _is_port_available(port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return True
        except OSError:
            return False

    def _find_free_port(self) -> int:
        used = {w["port"] for w in self.workers}
        port = self.base_port
        while True:
            if port not in used and self._is_port_available(port):
                return port
            port += 1

    @staticmethod
    def _is_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def add_worker(self) -> dict[str, Any]:
        os.makedirs(LOGS_DIR, exist_ok=True)
        wid = self._next_worker_id()
        port = self._find_free_port()
        log_path = os.path.join(LOGS_DIR, f"worker-{wid}.log")
        log_file = open(log_path, "w")
        proc = subprocess.Popen(
            [sys.executable, "worker.py", "--server", self.hub_url, "--health-port", str(port)],
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        entry = {"id": wid, "port": port, "pid": proc.pid}
        self._config.setdefault("workers", []).append(entry)
        self._save()
        return entry

    def remove_worker(self, worker_id: str) -> bool:
        worker = next((w for w in self.workers if w["id"] == worker_id), None)
        if not worker:
            return False
        self._kill_process(worker["pid"])
        self._config["workers"] = [w for w in self.workers if w["id"] != worker_id]
        self._save()
        return True

    def remove_all(self) -> int:
        count = len(self.workers)
        for w in self.workers:
            self._kill_process(w["pid"])
        self._config["workers"] = []
        self._save()
        return count

    def scale_to(self, target: int) -> dict[str, Any]:
        current = len(self.workers)
        added: list[dict] = []
        removed: list[str] = []
        if target > current:
            for _ in range(target - current):
                added.append(self.add_worker())
        elif target < current:
            to_remove = list(reversed(self.workers))[: current - target]
            for w in to_remove:
                self.remove_worker(w["id"])
                removed.append(w["id"])
        return {"added": added, "removed": removed, "total": len(self.workers)}

    @staticmethod
    def _kill_process(pid: int) -> None:
        try:
            os.kill(pid, signal.SIGINT)
        except (OSError, ProcessLookupError):
            return
        deadline = time.time() + 2
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
                time.sleep(0.1)
            except (OSError, ProcessLookupError):
                return
        try:
            os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass

    async def get_worker_status(self, worker: dict[str, Any]) -> dict[str, Any]:
        alive = self._is_alive(worker["pid"])
        health = "unreachable"
        if alive:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"http://127.0.0.1:{worker['port']}/healthz", timeout=aiohttp.ClientTimeout(total=2)
                    ) as resp:
                        if resp.status == 200:
                            health = "connected"
                        else:
                            health = "disconnected"
            except Exception:
                health = "unreachable"
        return {
            "id": worker["id"],
            "port": worker["port"],
            "pid": worker["pid"],
            "alive": alive,
            "health": health,
        }

    async def get_all_status(self) -> list[dict[str, Any]]:
        tasks = [self.get_worker_status(w) for w in self.workers]
        if not tasks:
            return []
        return list(await asyncio.gather(*tasks))

    def recover(self) -> None:
        """Check PIDs on startup and clean up dead entries."""
        self._load()


pool: PoolManager | None = None


def get_pool() -> PoolManager:
    assert pool is not None
    return pool


async def get_config_handler(request: web.Request) -> web.Response:
    p = get_pool()
    return web.json_response({"hub_url": p.hub_url, "base_port": p.base_port})


async def set_config_handler(request: web.Request) -> web.Response:
    p = get_pool()
    body = await request.json()
    hub_url = body.get("hub_url", p.hub_url)
    base_port = body.get("base_port", p.base_port)
    p.set_config(hub_url, base_port)
    return web.json_response({"hub_url": hub_url, "base_port": base_port})


async def list_workers_handler(request: web.Request) -> web.Response:
    p = get_pool()
    statuses = await p.get_all_status()
    return web.json_response(statuses)


async def add_workers_handler(request: web.Request) -> web.Response:
    p = get_pool()
    body = await request.json() if request.content_length else {}
    count = body.get("count", 1)
    if not p.hub_url:
        return web.Response(status=400, text="hub_url not configured")
    added = [p.add_worker() for _ in range(count)]
    return web.json_response(added, status=201)


async def remove_worker_handler(request: web.Request) -> web.Response:
    p = get_pool()
    worker_id = request.match_info["id"]
    if p.remove_worker(worker_id):
        return web.Response(status=204)
    return web.Response(status=404, text="worker not found")


async def remove_all_workers_handler(request: web.Request) -> web.Response:
    p = get_pool()
    p.remove_all()
    return web.Response(status=204)


async def scale_handler(request: web.Request) -> web.Response:
    p = get_pool()
    body = await request.json()
    target = body.get("target", 0)
    if not p.hub_url:
        return web.Response(status=400, text="hub_url not configured")
    result = p.scale_to(target)
    return web.json_response(result)


async def index_redirect(request: web.Request) -> web.Response:
    raise web.HTTPFound("/static/manager.html")


def create_app() -> web.Application:
    global pool
    pool = PoolManager()
    pool.recover()

    static_dir = os.path.join(os.path.dirname(__file__), "static")

    app = web.Application()
    app.router.add_get("/", index_redirect)
    app.router.add_get("/api/config", get_config_handler)
    app.router.add_post("/api/config", set_config_handler)
    app.router.add_get("/api/workers", list_workers_handler)
    app.router.add_post("/api/workers", add_workers_handler)
    app.router.add_delete("/api/workers", remove_all_workers_handler)
    app.router.add_delete("/api/workers/{id}", remove_worker_handler)
    app.router.add_post("/api/scale", scale_handler)
    app.router.add_static("/static", static_dir)

    return app


def cmd_serve(args: argparse.Namespace) -> None:
    app = create_app()
    print(f"Worker Pool Manager running on http://0.0.0.0:{args.port}")
    web.run_app(app, host="0.0.0.0", port=args.port)


def cmd_init(args: argparse.Namespace) -> None:
    p = PoolManager()
    p.set_config(args.hub_url, args.base_port)
    print(f"Initialized pool config: hub_url={args.hub_url}, base_port={args.base_port}")


def cmd_add(args: argparse.Namespace) -> None:
    p = PoolManager()
    if not p.hub_url:
        print("Error: hub_url not configured. Run 'init' first.")
        sys.exit(1)
    for _ in range(args.count):
        entry = p.add_worker()
        print(f"Started worker {entry['id']} on port {entry['port']} (pid {entry['pid']})")


def cmd_remove(args: argparse.Namespace) -> None:
    p = PoolManager()
    if args.id:
        if p.remove_worker(args.id):
            print(f"Stopped worker {args.id}")
        else:
            print(f"Worker {args.id} not found")
            sys.exit(1)
    else:
        workers = list(reversed(p.workers))[:args.count]
        for w in workers:
            p.remove_worker(w["id"])
            print(f"Stopped worker {w['id']}")


def cmd_status(_args: argparse.Namespace) -> None:
    p = PoolManager()
    if not p.workers:
        print("No workers in pool")
        return
    statuses = asyncio.run(p.get_all_status())
    print(f"Worker Pool (hub: {p.hub_url})")
    print(f"{'ID':<6} {'Port':<7} {'PID':<8} {'Process':<10} {'Health'}")
    for s in statuses:
        alive_str = "alive" if s["alive"] else "dead"
        pid_str = str(s["pid"]) if s["alive"] else "--"
        print(f"{s['id']:<6} {s['port']:<7} {pid_str:<8} {alive_str:<10} {s['health']}")


def cmd_stop_all(_args: argparse.Namespace) -> None:
    p = PoolManager()
    count = p.remove_all()
    print(f"Stopped {count} worker(s)")


def cmd_scale(args: argparse.Namespace) -> None:
    p = PoolManager()
    if not p.hub_url:
        print("Error: hub_url not configured. Run 'init' first.")
        sys.exit(1)
    result = p.scale_to(args.target)
    for w in result.get("added", []):
        print(f"Started worker {w['id']} on port {w['port']} (pid {w['pid']})")
    for wid in result.get("removed", []):
        print(f"Stopped worker {wid}")
    print(f"Pool now has {result['total']} worker(s)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Worker Pool Manager")
    subs = parser.add_subparsers(dest="command")

    serve_p = subs.add_parser("serve", help="Run the web UI")
    serve_p.add_argument("--port", type=int, default=9090, help="Port for the manager UI (default 9090)")

    init_p = subs.add_parser("init", help="Initialize pool config")
    init_p.add_argument("--hub-url", required=True, help="WebSocket URL of the hub")
    init_p.add_argument("--base-port", type=int, default=8081, help="Starting port for workers (default 8081)")

    add_p = subs.add_parser("add", help="Add worker(s) to the pool")
    add_p.add_argument("--count", type=int, default=1, help="Number of workers to add (default 1)")

    remove_p = subs.add_parser("remove", help="Remove worker(s) from the pool")
    remove_p.add_argument("--id", default=None, help="ID of a specific worker to remove")
    remove_p.add_argument("--count", type=int, default=1, help="Number of workers to remove from the end (default 1)")

    subs.add_parser("status", help="Show status of all workers")
    subs.add_parser("stop-all", help="Stop all workers")

    scale_p = subs.add_parser("scale", help="Scale pool to target size")
    scale_p.add_argument("target", type=int, help="Target number of workers")

    args = parser.parse_args()

    commands = {
        "serve": cmd_serve,
        "init": cmd_init,
        "add": cmd_add,
        "remove": cmd_remove,
        "status": cmd_status,
        "stop-all": cmd_stop_all,
        "scale": cmd_scale,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()

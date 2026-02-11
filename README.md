# 

This is an AI agent platform that pairs Claude (Anthropic) with a distributed pool of tool-executing workers. Users interact through a web dashboard that supports multiple concurrent sessions, while a hub routes tool calls to workers over WebSockets.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              Web UIs                                     │
│  ┌─────────────────────┐              ┌─────────────────────┐            │
│  │                     │              │                     │            │
│  │   (Agent Dashboard) │              │   (Worker Manager)  │            │
│  └────────┬────────────┘              └────────┬────────────┘            │
│           │ WS /sessions/{id}/chat             │ HTTP /api/*             │
└───────────┼────────────────────────────────────┼─────────────────────────┘
            ▼                                    ▼
┌──────────────────────────────────────┐  ┌──────────────────────────┐
│         server.py                    │  │  worker_manager.py       │
│                                      │  └──────────┬───────────────┘
│  ┌────────────────────────────────┐  │             │ spawns
│  │  Hub (hub.py)                  │  │             │
│  │  • Worker registry             │  │             ▼
│  │  • Tool schema aggregation     │  │  ┌──────────────────────────┐
│  │  • Session → worker affinity   │◄─┼──┤  worker.py (×N)          │
│  │  • Round-robin dispatch        │  │  │                          │
│  └────────────┬───────────────────┘  │  │  Built-in tools:         │
│  ┌────────────▼───────────────────┐  │  │  • read_file             │
│  │  Conversation (conversation.py)│  │  │  • list_directory        │
│  │  • Tool registration           │──┼──┤  • run_command           │
│  │  • Claude API calls            │  │  │                          │
│  └────────────┬───────────────────┘  │  └──────────────────────────┘
│  ┌────────────▼───────────────────┐  │
│  │  SessionStore (sessions.py)    │  │
│  └────────────────────────────────┘  │
└──────────────────────────────────────┘
            │
            ▼
   ┌─────────────────┐
   │  Anthropic API   │
   │  (Claude LLM)    │
   └─────────────────┘
```

### Components

| File | Role |
|---|---|
| `server.py` | HTTP/WebSocket server — serves the dashboard, exposes session CRUD, and hosts the hub's worker endpoint |
| `hub.py` | Worker registry and tool dispatch — aggregates tool schemas from connected workers, picks a worker per call (session affinity, then round-robin), and bridges futures between the conversation and workers |
| `conversation.py` | LLM orchestration — maintains message history, calls the Claude API, and runs the tool-use loop until the model stops requesting tools |
| `sessions.py` | File-based session persistence — stores conversation state as JSON under `sessions/` |
| `tools.py` | Built-in tool definitions — `read_file`, `list_directory`, `run_command` |
| `worker.py` | Tool worker process — connects to the hub via WebSocket, registers its tools, and executes tool calls on demand |
| `worker_manager.py` | Worker pool manager — spawns/stops worker subprocesses, exposes a management API and UI |
| `main.py` | Standalone CLI — run a one-shot agent or interactive chat without the web stack |

### Request flow

1. User sends a message through the dashboard WebSocket.
2. `server.py` loads the session's `Conversation` and registers the hub's tools on it.
3. The agent loop calls Claude. If Claude requests a tool, the hub dispatches the call to a worker (preferring the worker already affine to that session).
4. The worker executes the tool and returns the result over WebSocket.
5. The loop feeds the result back to Claude and repeats until Claude produces a final text response.
6. The session is saved and the response is streamed back to the browser.

## Quick start

```bash
# 1. Set your API key
echo "ANTHROPIC_API_KEY=sk-..." > .env

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run everything (server + 5 workers + manager UI)
./start.sh
```

This starts:
- **Agent dashboard** at `http://localhost:8080/static/index.html`
- **Worker manager** at `http://localhost:9090/static/manager.html`

Pass a number to `start.sh` to change the worker count: `./start.sh 3`

## Deployment

A `render.yaml` is included for deploying to Render as two services:
- **uma-hub** — the server (`python server.py`)
- **uma-tool-worker** — one or more workers (`python worker.py --server $HUB_URL`)

Set `ANTHROPIC_API_KEY` on the hub and `HUB_URL` on each worker.

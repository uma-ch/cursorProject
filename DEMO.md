# Demo Guide

Start the system before any demo:

```bash
./start.sh 5
```

Open two browser tabs:
- **Hub dashboard**: http://localhost:8080/static/index.html
- **Worker manager**: http://localhost:9090/static/manager.html

---

## 1. Basic Agent Chat

**Setup**: Hub dashboard open.

1. Click **+ New Agent**
2. Type: `What files are in the current directory?`
3. Watch the chat show `tool_use` (calling list_directory), then `tool_result`, then the assistant's answer

**Point out**: The agent autonomously chose and invoked a tool, the worker went busy/idle in the sidebar.

---

## 2. Multi-Tool Chain

**Setup**: Hub dashboard open.

1. Create a new agent
2. Type: `Read README.md, then run wc -l on all .py files and summarize the results`
3. Watch multiple rounds of tool_use/tool_result appear (read_file, then run_command)

**Point out**: The agent chains multiple tools across several turns, each result streaming into the chat.

---

## 3. Long-Running Command with Cancel

**Setup**: Hub dashboard open, watch the Workers section in the sidebar.

1. Create a new agent
2. Type: `Run the command "sleep 60 && echo finished"`
3. Observe: Send button becomes **Cancel**, worker shows as **busy**
4. After a few seconds, click **Cancel**
5. Chat shows "Cancelled" message, input re-enables

**Point out**: The Cancel button, the worker going busy then back to idle, and the instant cancellation.

---

## 4. Multiple Concurrent Agents

**Setup**: Hub dashboard and worker manager side-by-side.

1. Create 3 new agents (click + New Agent three times)
2. In each tab, quickly type a long-running command:
   - Agent 1: `Run "sleep 10 && echo agent-one"`
   - Agent 2: `Run "sleep 10 && echo agent-two"`
   - Agent 3: `Run "sleep 10 && echo agent-three"`
3. Watch the worker manager -- different workers go busy

**Point out**: Worker IDs match between hub sidebar and worker manager. Session affinity pins each agent to a worker. Multiple tasks run in parallel.

---

## 5. Worker Resilience

**Setup**: Hub dashboard and worker manager side-by-side.

1. Create an agent, type: `Run "sleep 30 && echo done"`
2. While it's running, go to the worker manager and click the stop button on the busy worker
3. Watch the agent chat -- it immediately shows an error ("worker disconnected") instead of hanging

**Point out**: The agent fails fast (no 120s timeout wait). The worker disappears from the hub sidebar.

---

## 6. Scaling Workers

**Setup**: Worker manager open.

1. Scale down to 1 worker (type 1 in the scale field, click Scale)
2. Create 3 agents, give each a task -- they queue up on the single worker
3. Scale up to 5 workers (type 5, click Scale)
4. New workers appear, register with the hub
5. Give the agents new tasks -- now they run in parallel

**Point out**: Workers auto-register with the hub when added. More workers = more parallelism.

---

## 7. Session Persistence

**Setup**: Hub dashboard open.

1. Create an agent, have a conversation (2-3 messages)
2. Close the browser tab entirely
3. Stop everything with Ctrl-C in the terminal
4. Restart: `./start.sh 5`
5. Open the dashboard -- sessions appear in the sidebar
6. Click a session -- full chat history loads from disk

**Point out**: Sessions survive server restarts. History is persisted to the `sessions/` directory.

---

## 8. Practical Use Case

**Setup**: Hub dashboard open.

1. Create a new agent
2. Type: `Write a Python script to /tmp/fib.py that prints the first 20 Fibonacci numbers, then run it and show me the output`
3. Watch the agent: it calls `run_command` to write the file, then calls `run_command` again to execute it
4. The final response includes the Fibonacci sequence

**Point out**: The agent writes and executes code autonomously using the shell tool.

---

## Running Automated Tests

To run all scenarios programmatically (with the system already running):

```bash
python test_demos.py
```

This runs all 8 scenarios and reports pass/fail for each.

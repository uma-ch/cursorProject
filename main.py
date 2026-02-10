#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys

from conversation import Conversation
from tools import ALL_TOOLS


async def chat_mode(conv: Conversation) -> None:
    print("Chat mode. Type 'quit' or Ctrl-D to exit.\n")
    loop = asyncio.get_event_loop()
    while True:
        try:
            user_input = await loop.run_in_executor(None, input, "you> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user_input.strip().lower() in ("quit", "exit"):
            break
        response = await conv.send(user_input)
        for block in response.content:
            if block.type == "text":
                print(f"\nassistant> {block.text}\n")


async def agent_mode(conv: Conversation, prompt: str) -> None:
    print(f"Agent mode. Running prompt: {prompt!r}\n")
    result = await conv.run_until_done(prompt)
    print(f"assistant> {result}")


def register_local_tools(conv: Conversation) -> None:
    for schema, handler in ALL_TOOLS:
        conv.register_tool(schema, handler)


async def async_main(args: argparse.Namespace) -> None:
    conv = Conversation(
        model=args.model,
        system=args.system,
        max_tokens=args.max_tokens,
    )

    hub = None
    if args.listen:
        from hub import Hub

        host, _, port_str = args.listen.rpartition(":")
        host = host or "0.0.0.0"
        port = int(port_str)
        hub = Hub(conv, host=host, port=port)
        await hub.start()
        print("Waiting for at least 1 worker to connect...")
        await hub.wait_for_workers(1)
    else:
        register_local_tools(conv)

    try:
        if args.mode == "chat":
            await chat_mode(conv)
        else:
            prompt = args.prompt
            if prompt is None:
                if sys.stdin.isatty():
                    print("Enter prompt (Ctrl-D to submit):")
                prompt = sys.stdin.read().strip()
                if not prompt:
                    print("Error: no prompt provided.", file=sys.stderr)
                    sys.exit(1)
            await agent_mode(conv, prompt)
    finally:
        if hub:
            await hub.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Anthropic conversation manager")
    parser.add_argument(
        "mode",
        choices=["chat", "agent"],
        help="'chat' for interactive REPL, 'agent' for agentic tool loop",
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        default=None,
        help="Initial prompt for agent mode (reads stdin if omitted in agent mode)",
    )
    parser.add_argument("--model", default="claude-sonnet-4-20250514")
    parser.add_argument("--system", default=None, help="System prompt")
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument(
        "--listen",
        default=None,
        metavar="HOST:PORT",
        help="Start WebSocket hub on HOST:PORT (e.g. 0.0.0.0:9600) and wait for remote tool workers",
    )
    args = parser.parse_args()

    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()

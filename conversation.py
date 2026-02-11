from __future__ import annotations

#import asyncio
import inspect
import json
from typing import Any, Callable

import anthropic


class Conversation:
    def __init__(
        self,
        *,
        model: str = "claude-sonnet-4-20250514",
        system: str | None = None,
        max_tokens: int = 8192,
        tools: list[dict] | None = None,
        tool_handlers: dict[str, Callable[..., Any]] | None = None,
    ):
        self.client = anthropic.AsyncAnthropic()
        self.model = model
        self.system = system
        self.max_tokens = max_tokens
        self.messages: list[dict] = []
        self.tools: list[dict] = tools or []
        self.tool_handlers: dict[str, Callable[..., Any]] = tool_handlers or {}

    def register_tool(self, schema: dict, handler: Callable[..., Any]) -> None:
        self.tools.append(schema)
        self.tool_handlers[schema["name"]] = handler

    async def _create(self) -> anthropic.types.Message:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": self.messages,
        }
        if self.system:
            kwargs["system"] = self.system
        if self.tools:
            kwargs["tools"] = self.tools
        return await self.client.messages.create(**kwargs)

    async def send(self, user_text: str) -> anthropic.types.Message:
        self.messages.append({"role": "user", "content": user_text})
        response = await self._create()
        self.messages.append({"role": "assistant", "content": response.content})
        return response

    async def step(self) -> anthropic.types.Message:
        response = await self._create()
        self.messages.append({"role": "assistant", "content": response.content})
        return response

    async def _call_handler(self, handler: Callable[..., Any], **kwargs: Any) -> Any:
        result = handler(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def _handle_tool_use(self, response: anthropic.types.Message) -> None:
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            handler = self.tool_handlers.get(block.name)
            if handler is None:
                result = f"Error: no handler registered for tool '{block.name}'"
            else:
                try:
                    result = await self._call_handler(handler, **block.input)
                except Exception as e:
                    result = f"Error: {e}"
            if not isinstance(result, str):
                result = json.dumps(result)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                }
            )
        if tool_results:
            self.messages.append({"role": "user", "content": tool_results})

    async def run_until_done(self, user_text: str) -> str:
        response = await self.send(user_text)

        while response.stop_reason == "tool_use":
            await self._handle_tool_use(response)
            response = await self.step()

        text_parts = [b.text for b in response.content if b.type == "text"]
        return "\n".join(text_parts)

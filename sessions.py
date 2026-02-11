from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from conversation import Conversation


def _serialize_messages(messages: list[dict]) -> list[dict]:
    serialized = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        if role == "assistant" and isinstance(content, list):
            content = [
                block.model_dump() if hasattr(block, "model_dump") else block
                for block in content
            ]
        serialized.append({"role": role, "content": content})
    return serialized


class SessionStore:
    def __init__(self, directory: str = "sessions"):
        self._dir = directory
        os.makedirs(self._dir, exist_ok=True)

    def _path(self, session_id: str) -> str:
        return os.path.join(self._dir, f"{session_id}.json")

    def create(
        self,
        model: str = "claude-sonnet-4-20250514",
        system: str | None = None,
        max_tokens: int = 8192,
    ) -> str:
        session_id = str(uuid.uuid4())
        data = {
            "session_id": session_id,
            "model": model,
            "system": system,
            "max_tokens": max_tokens,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "messages": [],
        }
        with open(self._path(session_id), "w") as f:
            json.dump(data, f, indent=2)
        return session_id

    def load(self, session_id: str) -> Conversation:
        with open(self._path(session_id), "r") as f:
            data = json.load(f)
        conv = Conversation(
            model=data["model"],
            system=data.get("system"),
            max_tokens=data.get("max_tokens", 8192),
        )
        conv.messages = data["messages"]
        return conv

    def save(self, session_id: str, conv: Conversation) -> None:
        with open(self._path(session_id), "r") as f:
            data = json.load(f)
        data["messages"] = _serialize_messages(conv.messages)
        with open(self._path(session_id), "w") as f:
            json.dump(data, f, indent=2)

    def get(self, session_id: str) -> dict[str, Any]:
        with open(self._path(session_id), "r") as f:
            return json.load(f)

    def list_all(self) -> list[dict[str, Any]]:
        sessions = []
        for filename in sorted(os.listdir(self._dir)):
            if not filename.endswith(".json"):
                continue
            with open(os.path.join(self._dir, filename), "r") as f:
                data = json.load(f)
            sessions.append({
                "session_id": data["session_id"],
                "model": data["model"],
                "system": data.get("system"),
                "created_at": data["created_at"],
                "message_count": len(data["messages"]),
            })
        return sessions

    def delete(self, session_id: str) -> None:
        path = self._path(session_id)
        if os.path.exists(path):
            os.remove(path)

    def exists(self, session_id: str) -> bool:
        return os.path.exists(self._path(session_id))

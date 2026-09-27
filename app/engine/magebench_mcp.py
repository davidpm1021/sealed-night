from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Any


class BridgeMcpError(RuntimeError):
    pass


class BridgeMcpClient:
    """Minimal JSON-RPC client for mage-bench's headless XMage bridge."""

    def __init__(self, endpoint: str, *, timeout: float = 30.0) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self._counter = 0
        self._lock = threading.Lock()

    def _next_id(self) -> int:
        with self._lock:
            self._counter += 1
            return self._counter

    def _rpc(self, method: str, params: dict[str, Any] | None = None, *, timeout: float | None = None) -> dict:
        request_id = self._next_id()
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": request_id}
        if params is not None:
            payload["params"] = params
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise BridgeMcpError(f"XMage bridge request failed for {method}: {exc}") from exc
        if data.get("error") is not None:
            raise BridgeMcpError(f"XMage bridge error for {method}: {data['error']}")
        result = data.get("result")
        if not isinstance(result, dict):
            raise BridgeMcpError(f"XMage bridge returned an invalid result for {method}.")
        return result

    def initialize(self) -> dict:
        return self._rpc(
            "initialize",
            {"protocolVersion": "2024-11-05", "capabilities": {}},
        )

    def list_tools(self) -> list[dict]:
        result = self._rpc("tools/list", {})
        tools = result.get("tools", [])
        if not isinstance(tools, list):
            raise BridgeMcpError("XMage bridge tools/list response was invalid.")
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None, *, timeout: float | None = None) -> str:
        result = self._rpc(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            timeout=timeout,
        )
        content = result.get("content") or []
        if not content or not isinstance(content, list):
            raise BridgeMcpError(f"XMage bridge tool {name} returned no content.")
        first = content[0]
        if not isinstance(first, dict) or "text" not in first:
            raise BridgeMcpError(f"XMage bridge tool {name} returned invalid content.")
        return str(first["text"])

    def call_tool_json(self, name: str, arguments: dict[str, Any] | None = None, *, timeout: float | None = None) -> Any:
        text = self.call_tool(name, arguments, timeout=timeout)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}

    def get_game_state(self) -> Any:
        return self.call_tool_json("get_game_state")

    def get_action_choices(self) -> Any:
        return self.call_tool_json("get_action_choices")

    def choose_action(self, **arguments: Any) -> Any:
        return self.call_tool_json("choose_action", arguments, timeout=600)

    def pass_priority(self, **arguments: Any) -> Any:
        return self.call_tool_json("pass_priority", arguments, timeout=900)

    def send_chat_message(self, message: str) -> Any:
        return self.call_tool_json("send_chat_message", {"message": message})

    def concede(self) -> Any:
        return self.call_tool_json("concede")

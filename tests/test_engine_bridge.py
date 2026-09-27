from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from app.engine.magebench_mcp import BridgeMcpClient


class FakeBridgeHandler(BaseHTTPRequestHandler):
    requests: list[dict] = []

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(length))
        type(self).requests.append(payload)

        if payload["method"] == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {}}
        elif payload["method"] == "tools/list":
            result = {"tools": [{"name": "get_game_state"}, {"name": "choose_action"}]}
        else:
            name = payload["params"]["name"]
            result = {"content": [{"type": "text", "text": json.dumps({"tool": name, "ok": True})}]}

        data = json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        return


def test_bridge_mcp_json_rpc_roundtrip():
    FakeBridgeHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeBridgeHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = BridgeMcpClient(f"http://127.0.0.1:{server.server_port}/mcp")
        initialized = client.initialize()
        assert initialized["protocolVersion"] == "2024-11-05"
        assert [tool["name"] for tool in client.list_tools()] == ["get_game_state", "choose_action"]

        result = client.get_game_state()
        assert result == {"tool": "get_game_state", "ok": True}

        choice = client.choose_action(choice="0")
        assert choice["tool"] == "choose_action"
        last = FakeBridgeHandler.requests[-1]
        assert last["method"] == "tools/call"
        assert last["params"]["arguments"] == {"choice": "0"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

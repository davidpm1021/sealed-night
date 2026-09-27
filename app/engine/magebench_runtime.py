from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import secrets
import socket
import subprocess
import time
from typing import TextIO
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from app.engine.magebench_mcp import BridgeMcpClient


PINNED_MAGE_BENCH_COMMIT = "78e18d68688ff6496f2506e4cae54629b5ba994f"


class MageBenchRuntimeError(RuntimeError):
    pass


@dataclass
class BridgeProcess:
    username: str
    mcp_port: int
    process: subprocess.Popen
    log_file: TextIO

    @property
    def client(self) -> BridgeMcpClient:
        return BridgeMcpClient(f"http://127.0.0.1:{self.mcp_port}/mcp")


@dataclass
class MatchRuntime:
    table_id: str
    game_dir: Path
    player_a: BridgeProcess
    player_b: BridgeProcess


def _maven_command() -> str:
    command = shutil.which("mvn.cmd") or shutil.which("mvn")
    if not command:
        raise MageBenchRuntimeError("Maven is not installed or not on PATH.")
    return command


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def find_free_port(start: int, *, require_secondary_offset: int | None = None) -> int:
    for port in range(start, start + 1000):
        if not _port_is_free(port):
            continue
        if require_secondary_offset is not None and not _port_is_free(port + require_secondary_offset):
            continue
        return port
    raise MageBenchRuntimeError(f"Could not find a free local port starting at {start}.")


def wait_for_port(port: int, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise MageBenchRuntimeError(f"Timed out waiting for local port {port}.")


def make_server_config(source: Path, destination: Path, port: int) -> Path:
    tree = ET.parse(source)
    root = tree.getroot()
    server = root.find("server")
    if server is None:
        raise MageBenchRuntimeError("XMage server config is missing its <server> element.")
    server.set("port", str(port))
    server.set("secondaryBindPort", str(port + 8))
    destination.parent.mkdir(parents=True, exist_ok=True)
    tree.write(destination, encoding="UTF-8", xml_declaration=True)
    return destination


def _post_json(url: str, payload: dict, *, timeout: float) -> dict:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise MageBenchRuntimeError(f"XMage observer request failed ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise MageBenchRuntimeError(f"XMage observer request failed: {exc}") from exc


def _get_json(url: str, *, timeout: float) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise MageBenchRuntimeError(f"XMage observer request failed: {exc}") from exc


def bridge_username(player_id: str, seat: str) -> str:
    clean = "".join(ch for ch in player_id.lower() if ch.isalnum())[:8]
    clean = clean or "player"
    # XMage default config requires 3-14 lowercase alphanumeric/underscore chars.
    return f"{seat.lower()}_{clean}"[:14]


class MageBenchRuntime:
    """Owns one local XMage server and observer, then creates one table per match.

    The actual game decisions are made through one headless bridge process per
    player. The web application only talks to BridgeMcpClient, never XMage's
    internal Java API.
    """

    def __init__(
        self,
        *,
        repo_root: str | Path = ".vendor/mage-bench",
        work_root: str | Path = "data/xmage",
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.work_root = Path(work_root).resolve()
        self.server_port: int | None = None
        self.observer_health_port: int | None = None
        self.server_process: subprocess.Popen | None = None
        self.observer_process: subprocess.Popen | None = None
        self._server_log: TextIO | None = None
        self._observer_log: TextIO | None = None
        self._bridges: list[BridgeProcess] = []

    def validate_installation(self) -> None:
        required = [
            self.repo_root / "pom.xml",
            self.repo_root / "Mage.Server" / "config" / "config.xml",
            self.repo_root / "Mage.Client.Observer" / "pom.xml",
            self.repo_root / "Mage.Client.Bridge" / "pom.xml",
        ]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise MageBenchRuntimeError(
                "mage-bench is not installed. Run scripts/setup-xmage.ps1. Missing: "
                + ", ".join(missing)
            )
        _maven_command()
        if not (shutil.which("java.exe") or shutil.which("java")):
            raise MageBenchRuntimeError("Java is not installed or not on PATH.")

    @property
    def running(self) -> bool:
        return bool(
            self.server_process
            and self.server_process.poll() is None
            and self.observer_process
            and self.observer_process.poll() is None
        )

    def start(self) -> None:
        if self.running:
            return
        self.validate_installation()
        self.work_root.mkdir(parents=True, exist_ok=True)
        maven = _maven_command()

        self.server_port = find_free_port(17171, require_secondary_offset=8)
        server_dir = self.work_root / "server"
        server_dir.mkdir(parents=True, exist_ok=True)
        config_path = make_server_config(
            self.repo_root / "Mage.Server" / "config" / "config.xml",
            server_dir / "config.xml",
            self.server_port,
        )
        self._server_log = open(server_dir / "server.log", "w", encoding="utf-8")
        server_env = os.environ.copy()
        server_env.update(
            {
                "XMAGE_AI_PUPPETEER": "1",
                "XMAGE_AI_PUPPETEER_USER": "spectator",
                "XMAGE_AI_PUPPETEER_PASSWORD": "",
                "XMAGE_AI_PUPPETEER_SERVER": "127.0.0.1",
                "XMAGE_AI_PUPPETEER_PORT": str(self.server_port),
                "XMAGE_AI_PUPPETEER_DISABLE_WHATS_NEW": "1",
                "MAVEN_OPTS": " ".join(
                    [
                        "-Xmx768m",
                        "-Djava.awt.headless=true",
                        f"-Dxmage.config.path={config_path}",
                    ]
                ),
            }
        )
        self.server_process = subprocess.Popen(
            [maven, "-q", "exec:java"],
            cwd=self.repo_root / "Mage.Server",
            env=server_env,
            stdout=self._server_log,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_for_port(self.server_port, timeout=120)
        except Exception:
            self.stop()
            raise

        observer_dir = self.work_root / "observer"
        observer_dir.mkdir(parents=True, exist_ok=True)
        health_port_file = observer_dir / "health_port"
        health_port_file.unlink(missing_ok=True)
        self.observer_health_port = find_free_port(20000)
        self._observer_log = open(observer_dir / "observer.log", "w", encoding="utf-8")
        observer_env = os.environ.copy()
        observer_env.update(
            {
                "XMAGE_AI_PUPPETEER": "1",
                "XMAGE_AI_PUPPETEER_USER": "spectator",
                "XMAGE_AI_PUPPETEER_PASSWORD": "",
                "XMAGE_AI_PUPPETEER_SERVER": "127.0.0.1",
                "XMAGE_AI_PUPPETEER_PORT": str(self.server_port),
                "XMAGE_AI_PUPPETEER_DISABLE_WHATS_NEW": "1",
                "XMAGE_AI_PUPPETEER_SKIP_INIT_SHUFFLING": "true",
                "XMAGE_AI_PUPPETEER_WINS_NEEDED": "1",
                "MAVEN_OPTS": " ".join(
                    [
                        "--add-opens=java.base/java.io=ALL-UNNAMED",
                        "-Xmx512m",
                        "-Dxmage.aiPuppeteer.autoConnect=true",
                        "-Dxmage.aiPuppeteer.disableWhatsNew=true",
                        "-Dxmage.observer.noWindow=true",
                        "-Dxmage.observer.keepAlive=true",
                        f"-Dxmage.observer.healthPort={self.observer_health_port}",
                        f"-Dxmage.observer.healthPortFile={health_port_file}",
                        "-Dxmage.aiPuppeteer.server=127.0.0.1",
                        f"-Dxmage.aiPuppeteer.port={self.server_port}",
                        "-Dxmage.aiPuppeteer.user=spectator",
                        "-Dxmage.aiPuppeteer.password=",
                    ]
                ),
            }
        )
        self.observer_process = subprocess.Popen(
            [maven, "-q", "exec:java"],
            cwd=self.repo_root / "Mage.Client.Observer",
            env=observer_env,
            stdin=subprocess.PIPE,
            stdout=self._observer_log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            wait_for_port(self.observer_health_port, timeout=120)
            commands = _get_json(
                f"http://127.0.0.1:{self.observer_health_port}/wait-for-commands?timeout=120",
                timeout=125,
            )
            if commands.get("status") != "ready":
                raise MageBenchRuntimeError(f"Observer command loop did not become ready: {commands}")
            health = _get_json(
                f"http://127.0.0.1:{self.observer_health_port}/health?timeout=120",
                timeout=125,
            )
            if health.get("status") != "ready":
                raise MageBenchRuntimeError(f"Observer lobby did not become ready: {health}")
        except Exception:
            self.stop()
            raise

    def create_table(
        self,
        *,
        match_key: str,
        username_a: str,
        username_b: str,
        wins_needed: int = 2,
    ) -> tuple[str, Path]:
        if not self.running or self.observer_process is None or self.observer_process.stdin is None:
            raise MageBenchRuntimeError("XMage runtime is not running.")
        assert self.observer_health_port is not None
        game_dir = (self.work_root / "games" / match_key).resolve()
        game_dir.mkdir(parents=True, exist_ok=True)
        command = {
            "gameDir": str(game_dir),
            "playersConfig": {
                "players": [
                    {"type": "sleepwalker", "name": username_a},
                    {"type": "sleepwalker", "name": username_b},
                ],
                "gameType": "Two Player Duel",
                "deckType": "Limited",
            },
            "choosingPlayer": username_a,
            "skipInitShuffling": False,
            "winsNeeded": wins_needed,
        }
        self.observer_process.stdin.write(json.dumps(command, separators=(",", ":")) + "\n")
        self.observer_process.stdin.flush()
        response = _post_json(
            f"http://127.0.0.1:{self.observer_health_port}/wait-for-ready",
            {"gameDir": str(game_dir), "timeout": 120},
            timeout=125,
        )
        if not response.get("ready") or not response.get("tableId"):
            raise MageBenchRuntimeError(f"XMage observer did not create a table: {response}")
        return str(response["tableId"]), game_dir

    def start_bridge(
        self,
        *,
        username: str,
        deck_path: Path,
        table_id: str,
        label: str,
    ) -> BridgeProcess:
        if not self.running or self.server_port is None:
            raise MageBenchRuntimeError("XMage runtime is not running.")
        maven = _maven_command()
        mcp_port = find_free_port(19000)
        log_dir = self.work_root / "bridges"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = open(log_dir / f"{label}.log", "w", encoding="utf-8")
        env = os.environ.copy()
        env["MAVEN_OPTS"] = " ".join(
            [
                "--add-opens=java.base/java.io=ALL-UNNAMED",
                "-Xmx256m",
                "-Dxmage.bridge.server=127.0.0.1",
                f"-Dxmage.bridge.port={self.server_port}",
                f"-Dxmage.bridge.tableId={table_id}",
                f"-Dxmage.bridge.mcpPort={mcp_port}",
            ]
        )
        process = subprocess.Popen(
            [
                maven,
                "-q",
                f"-Dxmage.bridge.username={username}",
                f"-Dxmage.bridge.deck={deck_path.resolve()}",
                "exec:java",
            ],
            cwd=self.repo_root / "Mage.Client.Bridge",
            env=env,
            stdin=subprocess.PIPE,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            wait_for_port(mcp_port, timeout=120)
            bridge = BridgeProcess(username=username, mcp_port=mcp_port, process=process, log_file=log_file)
            bridge.client.initialize()
        except Exception:
            if process.poll() is None:
                process.terminate()
            log_file.close()
            raise
        self._bridges.append(bridge)
        return bridge

    def start_match(
        self,
        *,
        match_key: str,
        player_a_id: str,
        player_b_id: str,
        deck_a: Path,
        deck_b: Path,
    ) -> MatchRuntime:
        # Unique seats prevent a later round from reusing an old bridge login.
        nonce = secrets.token_hex(4)
        username_a = bridge_username(nonce, "a")
        username_b = bridge_username(nonce, "b")
        table_id, game_dir = self.create_table(
            match_key=match_key,
            username_a=username_a,
            username_b=username_b,
            wins_needed=1,
        )
        bridge_a = self.start_bridge(
            username=username_a,
            deck_path=deck_a,
            table_id=table_id,
            label=f"{match_key}-a",
        )
        try:
            bridge_b = self.start_bridge(
                username=username_b,
                deck_path=deck_b,
                table_id=table_id,
                label=f"{match_key}-b",
            )
        except Exception:
            self.stop_bridge(bridge_a)
            raise
        return MatchRuntime(
            table_id=table_id,
            game_dir=game_dir,
            player_a=bridge_a,
            player_b=bridge_b,
        )

    def stop_bridge(self, bridge: BridgeProcess) -> None:
        if bridge.process.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(bridge.process.pid), "/T", "/F"],
                               capture_output=True, check=False)
            else:
                bridge.process.terminate()
            try:
                bridge.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                bridge.process.kill()
        bridge.log_file.close()
        if bridge in self._bridges:
            self._bridges.remove(bridge)

    def stop_match(self, match: MatchRuntime) -> None:
        self.stop_bridge(match.player_a)
        self.stop_bridge(match.player_b)

    def stop(self) -> None:
        for bridge in reversed(self._bridges):
            try:
                if bridge.process.poll() is None:
                    bridge.process.terminate()
                    try:
                        bridge.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        bridge.process.kill()
            finally:
                bridge.log_file.close()
        self._bridges.clear()

        for process in [self.observer_process, self.server_process]:
            if process is not None and process.poll() is None:
                if process.stdin:
                    try:
                        process.stdin.close()
                    except Exception:
                        pass
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()

        self.observer_process = None
        self.server_process = None
        if self._observer_log:
            self._observer_log.close()
        if self._server_log:
            self._server_log.close()
        self._observer_log = None
        self._server_log = None

    def __enter__(self) -> "MageBenchRuntime":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

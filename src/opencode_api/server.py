from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8088
DEFAULT_OPENCODE_PORT = 4096
DEFAULT_WORKSPACE_ROOT = Path("/tmp/lap-opencode")
INFERENCE_BASE_URL_ENV = "OPENCODE_INFERENCE_BASE_URL"
INFERENCE_API_KEY_ENV = "OPENCODE_INFERENCE_API_KEY"


@dataclass
class Session:
    id: str
    base_url: str
    password: str
    workspace: Path
    process: subprocess.Popen[bytes]


class SessionManager:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        self.latest_session_id: str | None = None
        self.lock = threading.RLock()

    def create(self, request: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            self.stop_all()
            port = int(request.get("port") or self.next_port())
            title = str(request.get("title") or "OpenCode session")
            workspace = self.workspace_path(request)
            self.write_agent_context(workspace, request)
            password = secrets.token_urlsafe(24)
            base_url = f"http://127.0.0.1:{port}"

            process = self.start_opencode(workspace, port, password)
            try:
                body = self.request_json(
                    "POST",
                    f"{base_url}/session",
                    {"title": title},
                    self.basic_auth("opencode", password),
                )
                session_id = self.required_id(body)
            except Exception:
                self.stop_process(process)
                raise
            self.sessions[session_id] = Session(
                id=session_id,
                base_url=base_url,
                password=password,
                workspace=workspace,
                process=process,
            )
            self.latest_session_id = session_id
            body.setdefault("sandbox_id", session_id)
            body.setdefault("opencode_base_url", base_url)
            body.setdefault("workspace", str(workspace))
            return body

    def send_message(self, session_id: str, request: dict[str, Any]) -> dict[str, Any]:
        session = self.session(session_id)
        return self.request_json(
            "POST",
            f"{session.base_url}/session/{session.id}/message",
            request,
            self.basic_auth("opencode", session.password),
            timeout=120,
        )

    def event_stream(self) -> urllib.response.addinfourl:
        session = self.session(self.latest_session_id)
        request = urllib.request.Request(f"{session.base_url}/event", method="GET")
        request.add_header("authorization", self.basic_auth("opencode", session.password))
        request.add_header("accept", "text/event-stream")
        return urllib.request.urlopen(request, timeout=120)

    def session(self, session_id: str | None) -> Session:
        with self.lock:
            if session_id is None:
                raise RuntimeError("no OpenCode session has been created")
            try:
                return self.sessions[session_id]
            except KeyError as error:
                raise RuntimeError(f"unknown OpenCode session: {session_id}") from error

    def workspace_path(self, request: dict[str, Any]) -> Path:
        value = request.get("workspace")
        path = Path(str(value)) if value else DEFAULT_WORKSPACE_ROOT / f"run_{secrets.token_hex(8)}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_agent_context(self, workspace: Path, request: dict[str, Any]) -> None:
        content = agent_context_markdown(session_context(request))
        if content:
            (workspace / "AGENTS.md").write_text(content, encoding="utf-8")

    def next_port(self) -> int:
        return int(os.getenv("OPENCODE_PORT", DEFAULT_OPENCODE_PORT))

    def stop_all(self) -> None:
        for session in list(self.sessions.values()):
            self.stop_process(session.process)
        self.sessions.clear()
        self.latest_session_id = None

    def stop_process(self, process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def start_opencode(self, workspace: Path, port: int, password: str) -> subprocess.Popen[bytes]:
        binary = shutil.which("opencode")
        if binary is None:
            raise RuntimeError("opencode binary not found in PATH")

        env = os.environ.copy()
        env["OPENCODE_SERVER_PASSWORD"] = password
        if inference_base_url := os.getenv(INFERENCE_BASE_URL_ENV):
            env["ANTHROPIC_BASE_URL"] = anthropic_base_url(inference_base_url)
        if inference_api_key := os.getenv(INFERENCE_API_KEY_ENV):
            env["ANTHROPIC_API_KEY"] = inference_api_key
        process = subprocess.Popen(
            [binary, "serve", "--hostname", "0.0.0.0", "--port", str(port)],
            cwd=workspace,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.wait_until_reachable(f"http://127.0.0.1:{port}", process)
        return process

    def wait_until_reachable(self, base_url: str, process: subprocess.Popen[bytes]) -> None:
        deadline = time.time() + 20
        while time.time() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"opencode exited with status {process.returncode}")
            if self.reachable(base_url):
                return
            time.sleep(0.25)
        raise RuntimeError("timed out waiting for opencode server")

    def reachable(self, base_url: str) -> bool:
        try:
            urllib.request.urlopen(f"{base_url}/", timeout=2).close()
            return True
        except urllib.error.HTTPError:
            return True
        except Exception:
            return False

    def request_json(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None,
        auth: str,
        timeout: int = 10,
    ) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("content-type", "application/json")
        request.add_header("authorization", auth)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
        return json.loads(payload.decode("utf-8") or "{}")

    def required_id(self, body: dict[str, Any]) -> str:
        session_id = body.get("id")
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError("opencode /session response did not include id")
        return session_id

    def basic_auth(self, username: str, password: str) -> str:
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        return f"Basic {token}"


class Handler(BaseHTTPRequestHandler):
    manager: SessionManager

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            self.write_json(HTTPStatus.OK, {"ok": True, "service": "opencode-api"})
            return
        if path == "/event":
            if not self.authorized():
                self.write_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            self.proxy_events()
            return
        self.write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if not self.authorized():
            self.write_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        try:
            if path == "/session":
                self.write_json(HTTPStatus.CREATED, self.manager.create(self.read_json()))
                return
            prefix = "/session/"
            suffix = "/message"
            if path.startswith(prefix) and path.endswith(suffix):
                session_id = path[len(prefix) : -len(suffix)]
                self.write_json(
                    HTTPStatus.OK,
                    self.manager.send_message(session_id, self.read_json()),
                )
                return
            self.write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except Exception as error:
            self.write_json(HTTPStatus.BAD_GATEWAY, {"error": str(error)})

    def proxy_events(self) -> None:
        try:
            upstream = self.manager.event_stream()
            self.send_response(HTTPStatus.OK)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            while True:
                chunk = upstream.read(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except Exception as error:
            self.write_json(HTTPStatus.BAD_GATEWAY, {"error": str(error)})

    def authorized(self) -> bool:
        api_key = os.getenv("OPENCODE_API_KEY")
        if not api_key:
            return True
        return self.headers.get("authorization") == f"Bearer {api_key}"

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length") or "0")
        if length == 0:
            return {}
        body = self.rfile.read(length)
        value = json.loads(body.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def write_json(self, status: HTTPStatus, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve(host: str, port: int) -> ThreadingHTTPServer:
    Handler.manager = SessionManager()
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"opencode-api listening on http://{host}:{port}", flush=True)
    server.serve_forever()
    return server


def anthropic_base_url(value: str) -> str:
    base_url = value.rstrip("/")
    if base_url.endswith("/v1"):
        return base_url
    return f"{base_url}/v1"


def session_context(request: dict[str, Any]) -> dict[str, Any]:
    resources = request.get("resources")
    if isinstance(resources, dict):
        merged = dict(request)
        merged.update(resources)
        return merged
    return request


def agent_context_markdown(context: dict[str, Any]) -> str:
    sections: list[str] = []
    agent = context.get("agent")
    if isinstance(agent, dict):
        name = agent.get("name")
        agent_id = agent.get("id")
        if name or agent_id:
            sections.append(f"# Agent\n\nName: {name or ''}\nID: {agent_id or ''}".strip())
    if system := context.get("system"):
        sections.append(f"# Instructions\n\n{system}")
    if model := context.get("model"):
        sections.append(f"# Model\n\n{model}")
    for key, title in [
        ("tools", "Tools"),
        ("mcp_servers", "MCP Servers"),
        ("environment", "Environment"),
    ]:
        value = context.get(key)
        if value not in (None, [], {}):
            sections.append(f"# {title}\n\n```json\n{json.dumps(value, indent=2)}\n```")
    if not sections:
        return ""
    return "\n\n".join(sections) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", DEFAULT_PORT)))
    args = parser.parse_args()
    serve(args.host, args.port)


if __name__ == "__main__":
    main()

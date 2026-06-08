from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8088
DEFAULT_OPENCODE_PORT = 4096
DEFAULT_WORKSPACE_ROOT = Path("/tmp/lap-opencode")


@dataclass
class Auth:
    type: str
    username: str
    password: str


@dataclass
class Session:
    id: str
    sandbox_id: str
    opencode_base_url: str
    opencode_session_id: str
    auth: Auth
    workspace: str


class SessionManager:
    def __init__(self, mock: bool = False):
        self.mock = mock
        self.processes: dict[str, subprocess.Popen[bytes]] = {}

    def create(self, request: dict[str, Any]) -> Session:
        session_id = f"lapoc_{secrets.token_hex(8)}"
        port = int(request.get("port") or DEFAULT_OPENCODE_PORT)
        title = str(request.get("title") or "OpenCode session")
        workspace = self.workspace_path(request, session_id)
        password = secrets.token_urlsafe(24)
        base_url = f"http://127.0.0.1:{port}"

        if self.mock:
            opencode_session_id = f"mock_{session_id}"
        else:
            self.start_opencode(session_id, workspace, port, password)
            opencode_session_id = self.create_opencode_session(base_url, title, password)

        return Session(
            id=session_id,
            sandbox_id=session_id,
            opencode_base_url=base_url,
            opencode_session_id=opencode_session_id,
            auth=Auth(type="basic", username="opencode", password=password),
            workspace=str(workspace),
        )

    def workspace_path(self, request: dict[str, Any], session_id: str) -> Path:
        value = request.get("workspace")
        path = Path(str(value)) if value else DEFAULT_WORKSPACE_ROOT / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def start_opencode(self, session_id: str, workspace: Path, port: int, password: str) -> None:
        binary = shutil.which("opencode")
        if binary is None:
            raise RuntimeError("opencode binary not found; set OPENCODE_MOCK=1 for smoke tests")

        env = os.environ.copy()
        env["OPENCODE_SERVER_PASSWORD"] = password
        process = subprocess.Popen(
            [binary, "serve", "--hostname", "0.0.0.0", "--port", str(port)],
            cwd=workspace,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.processes[session_id] = process
        self.wait_for_health(f"http://127.0.0.1:{port}", process)

    def wait_for_health(self, base_url: str, process: subprocess.Popen[bytes]) -> None:
        deadline = time.time() + 20
        while time.time() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"opencode exited with status {process.returncode}")
            if self.is_reachable(base_url):
                return
            time.sleep(0.25)
        raise RuntimeError("timed out waiting for opencode server")

    def is_reachable(self, base_url: str) -> bool:
        try:
            urllib.request.urlopen(f"{base_url}/", timeout=2).close()
            return True
        except urllib.error.HTTPError:
            return True
        except Exception:
            return False

    def create_opencode_session(self, base_url: str, title: str, password: str) -> str:
        body = self.request_json(
            "POST",
            f"{base_url}/session",
            {"title": title},
            self.basic_auth("opencode", password),
        )
        session_id = body.get("id")
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError("opencode /session response did not include id")
        return session_id

    def request_json(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None,
        auth: str | None,
    ) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("content-type", "application/json")
        if auth:
            request.add_header("authorization", auth)
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = response.read()
        return json.loads(payload.decode("utf-8") or "{}")

    def basic_auth(self, username: str, password: str) -> str:
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        return f"Basic {token}"


class Handler(BaseHTTPRequestHandler):
    manager: SessionManager

    def do_GET(self) -> None:
        if self.path == "/health":
            self.write_json(HTTPStatus.OK, {"ok": True, "service": "opencode-api"})
            return
        self.write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/sessions":
            self.write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not self.authorized():
            self.write_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        try:
            payload = self.read_json()
            session = self.manager.create(payload)
            self.write_json(HTTPStatus.CREATED, asdict(session))
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


def serve(host: str, port: int, mock: bool) -> ThreadingHTTPServer:
    Handler.manager = SessionManager(mock=mock)
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"opencode-api listening on http://{host}:{port} mock={mock}", flush=True)
    server.serve_forever()
    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", DEFAULT_PORT)))
    parser.add_argument("--mock", action="store_true", default=os.getenv("OPENCODE_MOCK") == "1")
    args = parser.parse_args()
    serve(args.host, args.port, args.mock)


if __name__ == "__main__":
    main()

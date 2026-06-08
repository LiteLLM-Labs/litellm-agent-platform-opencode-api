import json
import os
import stat
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from opencode_api.server import Handler, SessionManager, ThreadingHTTPServer, anthropic_base_url


class ServerTest(unittest.TestCase):
    def test_health_session_and_message_proxy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.install_fake_opencode(Path(temp_dir))
            old_path = os.environ["PATH"]
            os.environ["PATH"] = f"{temp_dir}{os.pathsep}{old_path}"
            try:
                Handler.manager = SessionManager()
                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base = f"http://127.0.0.1:{server.server_port}"

                health = request("GET", f"{base}/health")
                self.assertIs(health["ok"], True)

                session = request("POST", f"{base}/session", {"title": "test", "port": 4123})
                self.assertEqual(session["id"], "ses_fake")
                self.assertEqual(session["opencode_base_url"], "http://127.0.0.1:4123")

                message = request(
                    "POST",
                    f"{base}/session/ses_fake/message",
                    {"parts": [{"type": "text", "text": "hello"}]},
                )
                self.assertEqual(message["info"]["sessionID"], "ses_fake")
                self.assertEqual(message["parts"][0]["text"], "hello")

                server.shutdown()
                server.server_close()
            finally:
                os.environ["PATH"] = old_path

    def test_inference_base_url_defaults_to_anthropic_v1_path(self):
        self.assertEqual(
            anthropic_base_url("https://litellm-rust.onrender.com"),
            "https://litellm-rust.onrender.com/v1",
        )
        self.assertEqual(
            anthropic_base_url("https://litellm-rust.onrender.com/v1"),
            "https://litellm-rust.onrender.com/v1",
        )

    def install_fake_opencode(self, directory: Path):
        script = directory / "opencode"
        script.write_text(
            f"""#!{sys.executable}
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

parser = argparse.ArgumentParser()
parser.add_argument("serve")
parser.add_argument("--hostname", default="127.0.0.1")
parser.add_argument("--port", type=int, required=True)
args = parser.parse_args()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.write({{"ok": True}})
            return
        if self.path == "/event":
            payload = "data: {{\\"type\\":\\"session.idle\\",\\"properties\\":{{\\"sessionID\\":\\"ses_fake\\"}}}}\\n\\n".encode()
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("content-length") or "0")
        body = json.loads(self.rfile.read(length).decode() or "{{}}")
        if self.path == "/session":
            self.write({{"id": "ses_fake"}})
            return
        if self.path == "/session/ses_fake/message":
            self.write({{"info": {{"sessionID": "ses_fake"}}, "parts": body.get("parts", [])}})
            return
        self.send_response(404)
        self.end_headers()

    def write(self, body):
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        return

ThreadingHTTPServer((args.hostname, args.port), Handler).serve_forever()
"""
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR)


def request(method, url, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("content-type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))

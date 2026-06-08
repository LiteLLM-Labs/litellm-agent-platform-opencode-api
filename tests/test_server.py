import json
import threading
import unittest
import urllib.request

from opencode_api.server import Handler, SessionManager, ThreadingHTTPServer


class ServerTest(unittest.TestCase):
    def test_health_and_mock_session(self):
        Handler.manager = SessionManager(mock=True)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"

        health = request("GET", f"{base}/health")
        self.assertIs(health["ok"], True)

        session = request("POST", f"{base}/sessions", {"title": "test"})
        self.assertTrue(session["id"].startswith("lapoc_"))
        self.assertEqual(session["sandbox_id"], session["id"])
        self.assertTrue(session["opencode_session_id"].startswith("mock_lapoc_"))
        self.assertEqual(session["auth"]["type"], "basic")

        server.shutdown()
        server.server_close()


def request(method, url, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("content-type", "application/json")
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))

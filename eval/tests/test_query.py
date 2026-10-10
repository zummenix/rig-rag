import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from eval.runner.httpquery import QueryClient


def closed_port() -> int:
    """A loopback port that was just released, so connects are refused fast."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def make_hit(index):
    return {
        "score": 0.9 - index * 0.01,
        "path": f"data/jj/doc{index}.md",
        "start_line": index,
        "end_line": index + 1,
        "chunk_index": index,
        "text": "body",
    }


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the test output quiet
        pass

    def _json(self, status, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/health":
            self._json(200, {"status": "ok"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path != "/api/query":
            self._json(404, {"error": "not found"})
            return
        if body.get("question") == "boom":
            self._json(400, {"error": "bad request"})
            return
        k = int(body.get("k", 7))
        self._json(200, [make_hit(i) for i in range(min(k, 3))])


class QueryClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.client = QueryClient("127.0.0.1", self.port)

    def tearDown(self):
        self.client.close()

    def test_health(self):
        self.assertTrue(self.client.health())

    def test_query_returns_hits_and_elapsed(self):
        hits, elapsed_ms = self.client.query("anything", 3)
        self.assertEqual(len(hits), 3)
        self.assertEqual(hits[0]["path"], "data/jj/doc0.md")
        self.assertGreaterEqual(elapsed_ms, 0.0)

    def test_query_caps_at_available_hits(self):
        hits, _ = self.client.query("anything", 20)
        self.assertEqual(len(hits), 3)

    def test_query_raises_on_non_200(self):
        with self.assertRaises(RuntimeError):
            self.client.query("boom", 7)

    def test_connection_error_for_dead_port(self):
        client = QueryClient("127.0.0.1", closed_port(), timeout=0.5)
        with self.assertRaises(ConnectionError):
            client.query("x", 1)
        self.assertFalse(client.health())


if __name__ == "__main__":
    unittest.main()

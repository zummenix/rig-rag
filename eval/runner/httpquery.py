"""Minimal HTTP client for the `rig-rag` retrieval API.

Only the standard library is used, so the harness runs from a bare Python with
no virtualenv. A single keep-alive connection is reused across calls; transient
disconnects are retried once with a fresh connection.
"""

from __future__ import annotations

import http.client
import json
import time


class QueryClient:
    """Client for `GET /api/health` and `POST /api/query`."""

    def __init__(self, host: str, port: int, timeout: float = 30.0) -> None:
        self.host = host
        self.port = int(port)
        self.timeout = timeout
        self._conn: http.client.HTTPConnection | None = None

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _send(self, method: str, path: str, payload: bytes | None) -> tuple[int, bytes]:
        headers = {"content-type": "application/json"} if payload is not None else {}
        last_error: Exception | None = None
        for _ in range(2):
            connection = self._conn or http.client.HTTPConnection(
                self.host, self.port, timeout=self.timeout
            )
            try:
                connection.request(method, path, body=payload, headers=headers)
                response = connection.getresponse()
                data = response.read()
                self._conn = connection
                return response.status, data
            except (http.client.HTTPException, OSError) as error:
                last_error = error
                try:
                    connection.close()
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    pass
                self._conn = None
        raise ConnectionError(
            f"{method} {path} to {self.host}:{self.port} failed: {last_error}"
        ) from last_error

    def health(self) -> bool:
        try:
            status, _ = self._send("GET", "/api/health", None)
        except ConnectionError:
            return False
        return status == 200

    def query(self, question: str, k: int) -> tuple[list[dict], float]:
        """Runs one query; returns `(hits, elapsed_ms)`.

        `elapsed_ms` is the wall-clock time for the HTTP exchange and response
        read (JSON parsing excluded), which is what the latency sweep records.
        """

        payload = json.dumps({"question": question, "k": k}).encode("utf-8")
        started = time.perf_counter()
        status, data = self._send("POST", "/api/query", payload)
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        if status != 200:
            raise RuntimeError(
                f"POST /api/query returned {status}: {data[:500].decode('utf-8', 'replace')}"
            )
        hits = json.loads(data)
        if not isinstance(hits, list):
            raise RuntimeError(f"POST /api/query returned non-list JSON: {hits!r}")
        return hits, elapsed_ms

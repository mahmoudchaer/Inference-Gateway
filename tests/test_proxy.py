import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi.testclient import TestClient

from inference_gateway.app import _upstream_path, create_app
from inference_gateway.config import Settings


class Handler(BaseHTTPRequestHandler):
    seen = {}

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        Handler.seen = {
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "body": json.loads(raw),
            "gateway_header": self.headers.get("X-Gateway-Optimization"),
        }
        data = b'data: {"ok":true}\n\n'
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        return


def test_forwards_codex_auth_and_stripped_body():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    class FakeJev:
        async def score_batch(self, request_text, units):
            from inference_gateway.jev import JevScores

            return JevScores(
                probabilities={unit.key: 0.0 for unit in units},
                input_tokens=10,
                model="jev-test",
            )

    settings = Settings(
        host="127.0.0.1",
        port=0,
        upstream=f"http://127.0.0.1:{port}",
        optimization="normal",
        metrics_enabled=False,
        jev_model="jev-test",
        jev_base_url="http://jev.invalid",
        batch_size=8,
        jev_concurrency=2,
    )
    app = create_app(settings, jev_client=FakeJev())
    body = {
        "model": "gpt-5",
        "instructions": "stay",
        "input": [
            {"role": "user", "content": "old and unrelated"},
            {"role": "user", "content": "fix the bug"},
        ],
        "tools": [{"type": "function", "name": "shell", "description": "run"}],
        "stream": True,
    }
    with TestClient(app) as client:
        response = client.post(
            "/v1/responses",
            json=body,
            headers={"Authorization": "Bearer from-codex", "X-Gateway-Optimization": "high"},
        )
    server.shutdown()
    assert response.status_code == 200
    assert response.text.startswith("data:")
    assert Handler.seen["authorization"] == "Bearer from-codex"
    assert Handler.seen["gateway_header"] is None
    forwarded = Handler.seen["body"]
    assert forwarded["instructions"] == "stay"
    assert forwarded["input"] == [body["input"][-1]]
    assert forwarded["tools"] == []
    assert forwarded["stream"] is True


def test_settings_api_never_returns_the_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_DATA_DIR", str(tmp_path))
    settings = Settings(
        host="127.0.0.1", port=8080, upstream="https://example.invalid", optimization="normal",
        metrics_enabled=False, jev_model="jev-test", jev_base_url="https://jev.invalid",
        batch_size=8, jev_concurrency=2,
    )
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/settings", json={"optimization": "high", "logs_enabled": False, "jev_api_key": "secret"})
        assert response.status_code == 200
        assert response.json()["jev_api_key_set"] is True
        assert "secret" not in response.text
        fetched = client.get("/api/settings")
        assert fetched.json()["optimization"] == "high"
        assert "secret" not in fetched.text


def test_upstream_path_only_matches_the_chatgpt_hostname():
    assert _upstream_path("v1/responses", "https://chatgpt.com/backend-api/codex") == "responses"
    assert _upstream_path("v1/responses", "https://api.chatgpt.com") == "responses"
    assert _upstream_path("v1/responses", "https://chatgpt.com.evil.invalid") == "v1/responses"
    assert _upstream_path("v1/responses", "https://evil.invalid/chatgpt.com") == "v1/responses"

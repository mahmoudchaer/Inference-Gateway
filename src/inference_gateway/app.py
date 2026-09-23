from __future__ import annotations

import asyncio
import json
import ssl
import time
import uuid
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import certifi
import httpx
import websockets
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from inference_gateway.config import (
    Settings,
    data_dir,
    load_thresholds,
    read_config,
    write_config,
)
from inference_gateway.costs import input_cost
from inference_gateway.jev import JevClient
from inference_gateway.metrics import MetricsStore
from inference_gateway.optimize import optimize_payload
from inference_gateway.units import chat_from_payload

HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}

DASHBOARD = ( __import__("pathlib").Path(__file__).resolve().parent / "dashboard.html").read_text(encoding="utf-8")


def _level(request: Request, default: str) -> str:
    raw = request.headers.get("x-gateway-optimization") or request.query_params.get("optimization") or default
    raw = raw.strip().lower()
    return raw if raw in {"low", "normal", "high"} else default


def _forward_headers(request: Request, *, drop_encoding: bool) -> dict[str, str]:
    skip = set(HOP)
    if drop_encoding:
        skip.add("content-encoding")
    return {k: v for k, v in request.headers.items() if k.lower() not in skip and not k.lower().startswith("x-gateway-")}


def _decode_body(body: bytes, encoding: str | None) -> bytes:
    enc = (encoding or "").lower()
    if "zstd" in enc:
        import compression.zstd

        return compression.zstd.decompress(body)
    if "gzip" in enc:
        import gzip

        return gzip.decompress(body)
    return body


def create_app(settings: Settings | None = None, jev_client: JevClient | None = None, store: MetricsStore | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    thresholds = load_thresholds()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=30))
        app.state.jev = jev_client or JevClient(
            settings.jev_base_url,
            settings.jev_model or thresholds.get("model") or "jev-latest",
            app.state.http,
        )
        yield
        await app.state.http.aclose()

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings
    app.state.thresholds = thresholds
    app.state.metrics = store if store is not None else (MetricsStore(data_dir() / "metrics.db") if settings.metrics_enabled else None)

    @app.get("/")
    async def dashboard():
        return HTMLResponse(DASHBOARD)

    @app.get("/api/metrics")
    async def metrics_api():
        store_ = app.state.metrics
        if store_ is None:
            return {"enabled": False, "summary": None, "requests": []}
        return {"enabled": True, "summary": store_.summary(), "requests": store_.recent()}

    @app.get("/requests/{request_id}")
    async def request_page(request_id: str):
        return HTMLResponse(DASHBOARD)

    @app.get("/api/metrics/{request_id}")
    async def metric_detail(request_id: str):
        store_ = app.state.metrics
        if store_ is None:
            return JSONResponse({"error": "metrics disabled"}, status_code=404)
        row = store_.get(request_id)
        if row is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        detail = {}
        if row.get("detail"):
            try:
                detail = json.loads(row["detail"])
            except json.JSONDecodeError:
                detail = {}
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "model": row["model"],
            "optimization": row["optimization"],
            "original_tokens": row["original_tokens"],
            "optimized_tokens": row["optimized_tokens"],
            "tokens_removed": row["tokens_removed"],
            "reduction_pct": row["reduction_pct"],
            "note": row["note"],
            "removed": detail.get("removed") or [],
            "forwarded": detail.get("forwarded"),
            "original": detail.get("original"),
            "output": row.get("output") or "",
            "chat_id": row.get("chat_id") or "",
            "chat_title": row.get("chat_title") or "",
        }

    @app.get("/chats")
    async def chats_page():
        return HTMLResponse(DASHBOARD)

    @app.get("/chats/{chat_id}")
    async def chat_page(chat_id: str):
        return HTMLResponse(DASHBOARD)

    @app.get("/settings")
    async def settings_page():
        return HTMLResponse(DASHBOARD)

    @app.get("/api/settings")
    async def settings_api():
        saved = read_config(include_secret=True)
        saved["jev_api_key_set"] = bool(saved.pop("jev_api_key", ""))
        saved["restart_required"] = False
        return saved

    @app.post("/api/settings")
    async def update_settings(request: Request):
        try:
            incoming = await request.json()
        except (json.JSONDecodeError, UnicodeError):
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        if not isinstance(incoming, dict):
            return JSONResponse({"error": "expected an object"}, status_code=400)
        allowed = {key: incoming[key] for key in ("optimization", "logs_enabled", "jev_api_key") if key in incoming}
        try:
            saved = write_config(allowed)
        except (TypeError, ValueError):
            return JSONResponse({"error": "invalid settings"}, status_code=400)
        saved["jev_api_key_set"] = bool(saved.pop("jev_api_key", ""))
        saved["restart_required"] = True
        return saved

    @app.get("/api/chats")
    async def chats_api():
        store_ = app.state.metrics
        if store_ is None:
            return {"enabled": False, "chats": []}
        return {"enabled": True, "chats": store_.chats()}

    @app.get("/api/chats/{chat_id}")
    async def chat_api(chat_id: str):
        store_ = app.state.metrics
        if store_ is None:
            return JSONResponse({"error": "metrics disabled"}, status_code=404)
        rows = store_.for_chat(chat_id)
        if not rows:
            return JSONResponse({"error": "not found"}, status_code=404)
        title = next((row["chat_title"] for row in rows if row.get("chat_title")), "Untitled chat")
        return {"id": chat_id, "title": title, "requests": rows}

    @app.get("/health")
    async def health():
        return {"ok": True, "optimization": settings.optimization, "thresholds_calibrated": bool(thresholds.get("calibrated"))}

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def proxy(path: str, request: Request):
        raw_body = await request.body()
        encoding = request.headers.get("content-encoding")
        try:
            body = _decode_body(raw_body, encoding) if raw_body else raw_body
            decoded = body is not raw_body
        except Exception:
            body = raw_body
            decoded = False
        optimize = request.method == "POST" and path.rstrip("/") in {"v1/responses", "responses"}
        overhead_start = time.perf_counter()
        result = None
        forward_body = body
        if optimize and body:
            try:
                payload = json.loads(body)
            except (json.JSONDecodeError, UnicodeError):
                payload = None
            if isinstance(payload, dict):
                result = await optimize_payload(
                    payload,
                    level=_level(request, settings.optimization),
                    thresholds=app.state.thresholds,
                    jev=request.app.state.jev,
                    batch_size=settings.batch_size,
                    concurrency=settings.jev_concurrency,
                )
                forward_body = json.dumps(result.payload).encode()
                decoded = True
        overhead_ms = (time.perf_counter() - overhead_start) * 1000
        upstream = f"{settings.upstream}/{_upstream_path(path, settings.upstream)}"
        if request.url.query:
            upstream = f"{upstream}?{request.url.query}"
        upstream_req = request.app.state.http.build_request(
            request.method,
            upstream,
            headers=_forward_headers(request, drop_encoding=decoded),
            content=forward_body,
        )
        try:
            upstream_resp = await request.app.state.http.send(upstream_req, stream=True)
        except httpx.HTTPError:
            return JSONResponse({"error": {"message": "upstream request failed"}}, status_code=502)

        request_id = uuid.uuid4().hex if result is not None and app.state.metrics is not None else None
        response_headers = {k: v for k, v in upstream_resp.headers.items() if k.lower() not in HOP}

        async def stream():
            collected = bytearray()
            try:
                async for chunk in upstream_resp.aiter_raw():
                    if request_id is not None and len(collected) < 2_000_000:
                        collected.extend(chunk)
                    yield chunk
            finally:
                await upstream_resp.aclose()
                if request_id is not None:
                    snapshot = bytes(collected)
                    _spawn(_log_after_response(app.state.metrics, result, overhead_ms, request_id, payload, snapshot))

        return StreamingResponse(stream(), status_code=upstream_resp.status_code, headers=response_headers)

    @app.websocket("/{path:path}")
    async def websocket_proxy(websocket: WebSocket, path: str):
        await websocket.accept()
        headers = {
            key: value
            for key, value in websocket.headers.items()
            if key.lower() not in HOP and not key.lower().startswith("sec-websocket-")
        }
        upstream = settings.upstream
        if upstream.startswith("https://"):
            upstream = "wss://" + upstream.removeprefix("https://")
        elif upstream.startswith("http://"):
            upstream = "ws://" + upstream.removeprefix("http://")
        url = f"{upstream}/{_upstream_path(path, settings.upstream)}"
        if websocket.url.query:
            url = f"{url}?{websocket.url.query}"
        try:
            ssl_context = ssl.create_default_context(cafile=certifi.where()) if url.startswith("wss://") else None
            remote = await websockets.connect(
                url,
                additional_headers=headers,
                max_size=None,
                open_timeout=30,
                ssl=ssl_context,
            )
        except Exception as exc:
            print(f"websocket upstream failed: {exc}", flush=True)
            await websocket.close(code=1011)
            return

        outputs: dict[str, list[str]] = {}

        async def client_to_upstream():
            try:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    if message.get("text") is not None:
                        text, result = await _optimize_message(message["text"])
                        await remote.send(text)
                        if result is not None and app.state.metrics is not None:
                            request_id = uuid.uuid4().hex
                            outputs[request_id] = []
                            current.append(request_id)
                            _spawn(_log_after_response(
                                app.state.metrics,
                                result,
                                (time.perf_counter() - result.gateway_started) * 1000,
                                request_id,
                                result.original_payload,
                                b"",
                            ))
                    elif message.get("bytes") is not None:
                        await remote.send(message["bytes"])
            except WebSocketDisconnect:
                return

        async def _optimize_message(text: str):
            overhead_start = time.perf_counter()
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                return text, None
            if not isinstance(payload, dict) or not any(key in payload for key in ("input", "instructions", "tools")):
                return text, None
            result = await optimize_payload(
                payload,
                level=_level_from_headers(websocket.headers, settings.optimization),
                thresholds=app.state.thresholds,
                jev=app.state.jev,
                batch_size=settings.batch_size,
                concurrency=settings.jev_concurrency,
            )
            result.original_payload = payload
            result.gateway_started = overhead_start
            return json.dumps(result.payload), result

        current: list[str] = []

        async def upstream_to_client():
            try:
                async for message in remote:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)
                        if current:
                            piece = _output_from_event(message)
                            if piece:
                                outputs.setdefault(current[-1], []).append(piece)
            except WebSocketDisconnect:
                return

        client_task = asyncio.create_task(client_to_upstream())
        upstream_task = asyncio.create_task(upstream_to_client())
        done, pending = await asyncio.wait({client_task, upstream_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            if not task.cancelled():
                task.exception()
        if app.state.metrics is not None:
            for request_id, parts in outputs.items():
                if parts:
                    _spawn(asyncio.to_thread(app.state.metrics.set_output, request_id, "".join(parts)))
        try:
            await remote.close()
        except Exception:
            pass
        try:
            await websocket.close()
        except (RuntimeError, WebSocketDisconnect):
            pass

    return app


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _log_after_response(store, result, overhead_ms: float, request_id: str, original: dict, sse: bytes) -> None:
    def write() -> None:
        store.record(_metric_row(result, overhead_ms, request_id, original))
        if sse:
            store.set_output(request_id, _output_from_sse(sse))

    await asyncio.to_thread(write)


_background: set[asyncio.Task] = set()


def _metric_row(result, overhead_ms: float, request_id: str, original: dict) -> dict:
    model = str(result.payload.get("model") or "")
    before = input_cost(model, result.original_tokens)
    after = input_cost(model, result.optimized_tokens)
    chat_id, chat_title = chat_from_payload(original)
    return {
        "id": request_id,
        "model": model,
        "optimization": result.level,
        "original_tokens": result.original_tokens,
        "optimized_tokens": result.optimized_tokens,
        "jev_cost_usd": result.jev_cost_usd,
        "openai_cost_before": before,
        "openai_cost_after": after,
        "money_saved": before - after - result.jev_cost_usd,
        "jev_latency_ms": result.jev_latency_ms,
        "gateway_overhead_ms": overhead_ms,
        "note": result.note,
        "detail": json.dumps(_detail(original, result.payload), ensure_ascii=False),
        "chat_id": chat_id,
        "chat_title": chat_title,
    }


def _slice(payload: dict) -> dict:
    return {
        "instructions": payload.get("instructions"),
        "input": payload.get("input"),
        "tools": payload.get("tools"),
    }


def _removed_items(original, forwarded) -> list:
    kept: dict[str, int] = {}
    for item in forwarded if isinstance(forwarded, list) else []:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        kept[key] = kept.get(key, 0) + 1
    removed = []
    for item in original if isinstance(original, list) else []:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if kept.get(key, 0):
            kept[key] -= 1
        else:
            removed.append(item)
    return removed


def _detail(original: dict, forwarded: dict) -> dict:
    return {
        "original": _slice(original),
        "forwarded": _slice(forwarded),
        "removed": {
            "input": _removed_items(original.get("input"), forwarded.get("input")),
            "tools": _removed_items(original.get("tools"), forwarded.get("tools")),
        },
    }


def _output_from_sse(raw: bytes) -> str:
    pieces: list[str] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        piece = _output_from_event(data)
        if piece:
            pieces.append(piece)
    return "".join(pieces)


def _output_from_event(data: str) -> str:
    try:
        event = json.loads(data)
    except json.JSONDecodeError:
        return ""
    if not isinstance(event, dict):
        return ""
    kind = str(event.get("type") or "")
    if kind.endswith("output_text.delta") and isinstance(event.get("delta"), str):
        return event["delta"]
    if kind == "response.output_text.done" and isinstance(event.get("text"), str) and not event.get("delta"):
        return ""
    return ""


def _upstream_path(path: str, upstream: str) -> str:
    path = path.lstrip("/")
    hostname = (urlsplit(upstream).hostname or "").lower().rstrip(".")
    if (hostname == "chatgpt.com" or hostname.endswith(".chatgpt.com")) and path.startswith("v1/"):
        return path[3:]
    return path


def _level_from_headers(headers, default: str) -> str:
    raw = headers.get("x-gateway-optimization") or default
    raw = raw.strip().lower()
    return raw if raw in {"low", "normal", "high"} else default

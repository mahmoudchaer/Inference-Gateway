from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
  id TEXT PRIMARY KEY,
  created_at REAL NOT NULL,
  model TEXT,
  optimization TEXT,
  original_tokens INTEGER,
  optimized_tokens INTEGER,
  tokens_removed INTEGER,
  reduction_pct REAL,
  jev_cost_usd REAL,
  openai_cost_before REAL,
  openai_cost_after REAL,
  money_saved REAL,
  jev_latency_ms REAL,
  gateway_overhead_ms REAL,
  note TEXT
);
"""


class MetricsStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(requests)")}
            if "detail" not in columns:
                conn.execute("ALTER TABLE requests ADD COLUMN detail TEXT")
            if "output" not in columns:
                conn.execute("ALTER TABLE requests ADD COLUMN output TEXT")
            if "chat_id" not in columns:
                conn.execute("ALTER TABLE requests ADD COLUMN chat_id TEXT")
            if "chat_title" not in columns:
                conn.execute("ALTER TABLE requests ADD COLUMN chat_title TEXT")
            self._pending_output: dict[str, str] = {}
            self._backfill_chats(conn)
            self._reprice(conn)
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def record(self, row: dict) -> str:
        removed = max(0, int(row["original_tokens"]) - int(row["optimized_tokens"]))
        original = int(row["original_tokens"]) or 1
        request_id = row.get("id") or uuid.uuid4().hex
        payload = {
            "id": request_id,
            "created_at": time.time(),
            "model": row.get("model") or "",
            "optimization": row.get("optimization") or "",
            "original_tokens": original,
            "optimized_tokens": int(row["optimized_tokens"]),
            "tokens_removed": removed,
            "reduction_pct": round(100 * removed / original, 2),
            "jev_cost_usd": float(row.get("jev_cost_usd") or 0),
            "openai_cost_before": float(row.get("openai_cost_before") or 0),
            "openai_cost_after": float(row.get("openai_cost_after") or 0),
            "money_saved": float(row.get("money_saved") or 0),
            "jev_latency_ms": float(row.get("jev_latency_ms") or 0),
            "gateway_overhead_ms": float(row.get("gateway_overhead_ms") or 0),
            "note": row.get("note") or "",
            "detail": row.get("detail") or "",
            "output": self._pending_output.pop(request_id, row.get("output") or ""),
            "chat_id": row.get("chat_id") or "",
            "chat_title": row.get("chat_title") or "",
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO requests (
                  id, created_at, model, optimization, original_tokens, optimized_tokens,
                  tokens_removed, reduction_pct, jev_cost_usd, openai_cost_before,
                  openai_cost_after, money_saved,                   jev_latency_ms, gateway_overhead_ms, note,
                  detail, output, chat_id, chat_title
                ) VALUES (
                  :id, :created_at, :model, :optimization, :original_tokens, :optimized_tokens,
                  :tokens_removed, :reduction_pct, :jev_cost_usd, :openai_cost_before,
                  :openai_cost_after, :money_saved, :jev_latency_ms, :gateway_overhead_ms, :note,
                  :detail, :output, :chat_id, :chat_title
                )
                """,
                payload,
            )
        return request_id

    def set_output(self, request_id: str, output: str) -> None:
        with self._connect() as conn:
            updated = conn.execute(
                "UPDATE requests SET output = ? WHERE id = ?",
                (output, request_id),
            ).rowcount
        if not updated:
            self._pending_output[request_id] = output

    def get(self, request_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
        return dict(row) if row else None

    def _reprice(self, conn: sqlite3.Connection) -> None:
        from inference_gateway.costs import input_per_million

        rows = conn.execute(
            "SELECT id, model, original_tokens, optimized_tokens, jev_cost_usd FROM requests"
        ).fetchall()
        for row in rows:
            per_token = input_per_million(conn, row["model"] or "") / 1_000_000
            before = (row["original_tokens"] or 0) * per_token
            after = (row["optimized_tokens"] or 0) * per_token
            saved = before - after - (row["jev_cost_usd"] or 0)
            conn.execute(
                """
                UPDATE requests
                SET openai_cost_before = ?, openai_cost_after = ?, money_saved = ?
                WHERE id = ?
                """,
                (before, after, saved, row["id"]),
            )

    def _backfill_chats(self, conn: sqlite3.Connection) -> None:
        from inference_gateway.units import chat_from_payload

        rows = conn.execute("SELECT id, detail, chat_id FROM requests").fetchall()
        for row in rows:
            if not row["detail"]:
                continue
            try:
                detail = json.loads(row["detail"])
            except json.JSONDecodeError:
                continue
            original = detail.get("original")
            if not isinstance(original, dict):
                continue
            chat_id, title = chat_from_payload(original)
            if not chat_id:
                chat_id = row["chat_id"] or row["id"]
            conn.execute(
                "UPDATE requests SET chat_id = ?, chat_title = ? WHERE id = ?",
                (chat_id, title, row["id"]),
            )

    def chats(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  chat_id,
                  COUNT(*) AS requests,
                  MIN(created_at) AS started_at,
                  MAX(created_at) AS last_at,
                  COALESCE(SUM(original_tokens), 0) AS original_tokens,
                  COALESCE(SUM(tokens_removed), 0) AS tokens_removed,
                  COALESCE(SUM(money_saved), 0) AS money_saved,
                  (
                    SELECT chat_title FROM requests titled
                    WHERE titled.chat_id = requests.chat_id AND titled.chat_title != ''
                    ORDER BY titled.created_at ASC LIMIT 1
                  ) AS title
                FROM requests
                WHERE chat_id IS NOT NULL AND chat_id != ''
                GROUP BY chat_id
                ORDER BY last_at DESC
                """
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            original = item["original_tokens"] or 1
            item["reduction_pct"] = round(100 * item["tokens_removed"] / original, 2)
            item["title"] = item["title"] or "Untitled chat"
            result.append(item)
        return result

    def for_chat(self, chat_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, created_at, model, optimization, original_tokens, optimized_tokens,
                       tokens_removed, reduction_pct, jev_cost_usd, openai_cost_before,
                       openai_cost_after, money_saved, jev_latency_ms, gateway_overhead_ms, note,
                       chat_id, chat_title
                FROM requests WHERE chat_id = ? ORDER BY created_at ASC
                """,
                (chat_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent(self, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, created_at, model, optimization, original_tokens, optimized_tokens,
                       tokens_removed, reduction_pct, jev_cost_usd, openai_cost_before,
                       openai_cost_after, money_saved, jev_latency_ms, gateway_overhead_ms, note
                FROM requests ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def summary(self) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                  COUNT(*) AS requests,
                  COALESCE(SUM(original_tokens), 0) AS original_tokens,
                  COALESCE(SUM(optimized_tokens), 0) AS optimized_tokens,
                  COALESCE(SUM(tokens_removed), 0) AS tokens_removed,
                  COALESCE(SUM(jev_cost_usd), 0) AS jev_cost_usd,
                  COALESCE(SUM(openai_cost_before), 0) AS openai_cost_before,
                  COALESCE(SUM(openai_cost_after), 0) AS openai_cost_after,
                  COALESCE(SUM(money_saved), 0) AS money_saved,
                  COALESCE(AVG(jev_latency_ms), 0) AS jev_latency_ms,
                  COALESCE(AVG(gateway_overhead_ms), 0) AS gateway_overhead_ms
                FROM requests
                """
            ).fetchone()
        data = dict(row)
        original = data["original_tokens"] or 1
        data["reduction_pct"] = round(100 * data["tokens_removed"] / original, 2) if data["requests"] else 0
        return data

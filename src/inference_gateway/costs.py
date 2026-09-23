from __future__ import annotations

import sqlite3
from pathlib import Path

# Standard short-context input prices, USD per 1M tokens.
# https://developers.openai.com/api/docs/pricing
SEED = {
    "gpt-6-astra": 5.00,
    "gpt-6-sol": 1.00,
    "gpt-6-luna": 0.05,
    "gpt-5.6-luna": 0.20,
    "gpt-5.6-terra": 2.00,
    "gpt-5.6-sol": 4.00,
    "gpt-5.4": 2.50,
    "gpt-5.3-codex": 1.75,
    "gpt-5": 1.25,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS model_prices (
  model TEXT PRIMARY KEY,
  input_per_million REAL NOT NULL
);
"""


def ensure_prices(conn: sqlite3.Connection) -> None:
    conn.execute(SCHEMA)
    conn.executemany(
        "INSERT OR IGNORE INTO model_prices (model, input_per_million) VALUES (?, ?)",
        list(SEED.items()),
    )


def input_per_million(conn: sqlite3.Connection, model: str) -> float:
    ensure_prices(conn)
    rows = conn.execute("SELECT model, input_per_million FROM model_prices").fetchall()
    name = model or ""
    match = ""
    price = 0.0
    for row in rows:
        key = row[0]
        if name.startswith(key) and len(key) > len(match):
            match = key
            price = float(row[1])
    return price


def input_cost(model: str, tokens: int, db_path: Path | None = None) -> float:
    from inference_gateway.config import data_dir

    path = db_path or (data_dir() / "metrics.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        per_million = input_per_million(conn, model)
    return tokens * per_million / 1_000_000

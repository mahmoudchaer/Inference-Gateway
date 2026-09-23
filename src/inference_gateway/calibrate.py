"""Measure drop thresholds against labeled context units.

Requires TYPESAFE_API_KEY. Writes thresholds.json into the gateway data dir.
A unit labeled "drop" is unnecessary. A unit labeled "keep" is required.
"""

from __future__ import annotations

import asyncio
import json
import os

import httpx

from inference_gateway.config import data_dir, load_dotenv
from inference_gateway.jev import JevClient
from inference_gateway.units import Unit

# Obvious cases only. Thresholds are fit to Jev's probabilities on these, not chosen in advance.
FIXTURES = [
    ("Fix the typo in README line 3.", "The user previously asked how to bake sourdough.", "drop"),
    ("Fix the typo in README line 3.", "Earlier the user said the project is a Python CLI called acme.", "drop"),
    ("Rename the function load_user to fetch_user in api.py.", "api.py defines def load_user(id): return db.get(id).", "keep"),
    ("Why did the last test fail?", "pytest output: AssertionError in tests/test_api.py: expected 200 got 500.", "keep"),
    ("Add a README badge.", "Tool: shell. Runs a shell command on the user's machine.", "drop"),
    ("Run the tests and paste the failure.", "Tool: shell. Runs a shell command on the user's machine.", "keep"),
    ("Explain this stack trace.", "Unrelated tool result: weather in Lisbon is 18C.", "drop"),
    ("Explain this stack trace.", "Traceback: KeyError 'host' in gateway/config.py line 40.", "keep"),
]


def _pick(scored: list[tuple[float, str]]) -> dict[str, float]:
    keeps = [p for p, label in scored if label == "keep"]
    drops = [p for p, label in scored if label == "drop"]
    if not keeps or not drops:
        raise SystemExit("calibration fixtures did not produce both labels")
    lowest_keep = min(keeps)
    # Low: only drop what scored below every kept fixture, with a gap.
    low = min(drops) if min(drops) < lowest_keep else lowest_keep
    # Use the highest drop that still sits under every keep. If scores overlap, stay under the lowest keep.
    safe_drops = [p for p in drops if p < lowest_keep]
    if safe_drops:
        low = max(safe_drops)
        normal = min(lowest_keep, (max(safe_drops) + lowest_keep) / 2)
    else:
        low = lowest_keep * 0.25
        normal = lowest_keep * 0.5
    high = lowest_keep
    # High may remove a borderline keep; that is the accepted risk. Nudge it to the midpoint of the overlap if drops exceed keeps.
    high_drops = [p for p in drops if p >= lowest_keep]
    if high_drops:
        high = min(1.0, (lowest_keep + max(high_drops)) / 2)
    return {
        "low": round(min(low, normal, high), 4),
        "normal": round(min(max(normal, low), high), 4),
        "high": round(max(high, normal, low), 4),
    }


async def main() -> None:
    load_dotenv()
    if not os.environ.get("TYPESAFE_API_KEY", "").strip():
        raise SystemExit("Set TYPESAFE_API_KEY. The gateway does not use an OpenAI key.")
    model = os.environ.get("GATEWAY_JEV_MODEL", "jev-latest")
    async with httpx.AsyncClient() as http:
        client = JevClient(os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai").rstrip("/"), model, http)
        scored: list[tuple[float, str]] = []
        served = model
        for request_text, unit_text, label in FIXTURES:
            result = await client.score_batch(request_text, [Unit(key="u", kind="history", text=unit_text)])
            served = result.model or served
            probability = result.probabilities.get("u")
            if probability is None:
                raise SystemExit(f"Jev returned no probability for: {unit_text[:60]}")
            scored.append((probability, label))
            print(f"{label:4}  {probability:.3f}  {unit_text[:70]}")
    levels = _pick(scored)
    payload = {
        "model": served,
        "calibrated": True,
        "levels": levels,
        "fixtures": [{"p": p, "label": label} for p, label in scored],
    }
    path = data_dir() / "thresholds.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(levels, indent=2))
    print(f"wrote {path}")


if __name__ == "__main__":
    asyncio.run(main())

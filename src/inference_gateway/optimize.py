from __future__ import annotations

import time
from dataclasses import dataclass, field

from inference_gateway.jev import JevClient
from inference_gateway.units import Plan, Unit, apply_drops, plan_payload

JEV_USD_PER_TOKEN = 0.042 / 1_000_000


@dataclass
class OptimizationResult:
    payload: dict
    level: str
    kept: bool
    original_tokens: int
    optimized_tokens: int
    jev_cost_usd: float
    jev_latency_ms: float
    note: str = ""
    probabilities: dict[str, float] = field(default_factory=dict)


def estimate_tokens(payload: dict) -> int:
    import json

    parts = []
    for key in ("instructions", "input", "tools"):
        if key in payload and payload[key] is not None:
            parts.append(json.dumps(payload[key], ensure_ascii=False, separators=(",", ":")))
    return max(1, len("".join(parts)) // 4) if parts else 1


def dropped_keys(probabilities: dict[str, float], candidates: list[Unit], level: str, thresholds: dict) -> set[str]:
    """Return candidates Jev says are unnecessary at the selected risk level.

    Jev's `noul` value is an absolute probability that a unit is needed.  A
    missing, non-numeric, or out-of-range value is deliberately kept: an
    incomplete classifier response must never delete user context.
    """
    levels = thresholds.get("levels") if isinstance(thresholds, dict) else None
    if not isinstance(levels, dict):
        return set()
    cutoff = levels.get(level, levels.get("normal"))
    if not isinstance(cutoff, (int, float)) or isinstance(cutoff, bool):
        return set()
    cutoff = min(1.0, max(0.0, float(cutoff)))
    valid_keys = {unit.key for unit in candidates}
    dropped = set()
    for key, probability in probabilities.items():
        if key not in valid_keys or not isinstance(probability, (int, float)) or isinstance(probability, bool):
            continue
        value = float(probability)
        if 0.0 <= value <= 1.0 and value < cutoff:
            dropped.add(key)
    return dropped


async def _score_all(jev: JevClient, plan: Plan, batch_size: int, concurrency: int) -> tuple[dict[str, float], int, float]:
    import asyncio

    started = time.perf_counter()
    request_text = plan.user_message or plan.current_request
    groups = [(request_text, plan.candidates)]
    batches = []
    for request_text, units in groups:
        for index in range(0, len(units), batch_size):
            batches.append((request_text, units[index : index + batch_size]))
    semaphore = asyncio.Semaphore(concurrency)
    tokens = 0
    probabilities: dict[str, float] = {}

    async def one(request_text: str, batch: list[Unit]) -> None:
        nonlocal tokens
        async with semaphore:
            scored = await jev.score_batch(request_text, batch)
        tokens += scored.input_tokens
        probabilities.update(scored.probabilities)

    await asyncio.gather(*(one(request_text, batch) for request_text, batch in batches))
    return probabilities, tokens, (time.perf_counter() - started) * 1000


async def optimize_payload(
    payload: dict,
    *,
    level: str,
    thresholds: dict,
    jev: JevClient,
    batch_size: int,
    concurrency: int,
) -> OptimizationResult:
    original_tokens = estimate_tokens(payload)
    plan = plan_payload(payload)
    if not plan.user_message.strip() or not plan.candidates:
        return OptimizationResult(
            payload=payload,
            level=level,
            kept=True,
            original_tokens=original_tokens,
            optimized_tokens=original_tokens,
            jev_cost_usd=0,
            jev_latency_ms=0,
            note="nothing to score",
        )
    try:
        probabilities, jev_tokens, jev_ms = await _score_all(jev, plan, batch_size, concurrency)
    except Exception as exc:
        return OptimizationResult(
            payload=payload,
            level=level,
            kept=True,
            original_tokens=original_tokens,
            optimized_tokens=original_tokens,
            jev_cost_usd=0,
            jev_latency_ms=0,
            note=f"forwarded unchanged: {exc}",
        )

    dropped = dropped_keys(probabilities, plan.candidates, level, thresholds)
    updated = apply_drops(payload, dropped, plan)
    return OptimizationResult(
        payload=updated,
        level=level,
        kept=not dropped,
        original_tokens=original_tokens,
        optimized_tokens=estimate_tokens(updated),
        jev_cost_usd=jev_tokens * JEV_USD_PER_TOKEN,
        jev_latency_ms=jev_ms,
        probabilities=probabilities,
    )

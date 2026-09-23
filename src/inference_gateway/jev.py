from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

from inference_gateway.units import Unit

_CAP = 4000
_INSTRUCTION = (
    "This earlier context is needed for the current user message. "
    "Needed means the answer would be wrong or incomplete without it. "
    "Unrelated or stale context is not needed."
)
_TOOL_INSTRUCTION = (
    "This tool will be needed to carry out the current user message. "
    "Needed means the assistant would call it while handling that message."
)


@dataclass
class JevScores:
    probabilities: dict[str, float]
    input_tokens: int
    model: str


class JevError(Exception):
    pass


def _clip(text: str) -> str:
    text = text.strip()
    if len(text) <= _CAP:
        return text
    return text[:_CAP] + "\n…[truncated for scoring]"


class JevClient:
    def __init__(self, base_url: str, model: str, client: httpx.AsyncClient):
        self.base_url = base_url
        self.model = model
        self.client = client

    async def score_batch(self, request_text: str, units: list[Unit]) -> JevScores:
        key = os.environ.get("TYPESAFE_API_KEY", "").strip()
        if not key:
            raise JevError("TYPESAFE_API_KEY is not set")
        state = {
            "request": _clip(request_text),
            "units": [{"id": unit.key, "kind": unit.kind, "text": _clip(unit.text)} for unit in units],
        }
        questions = {}
        for unit in units:
            if unit.kind == "tool":
                questions[unit.key] = {
                    "type": "noul",
                    "instructions": f"Tool {unit.key} will be needed for the user message. {_TOOL_INSTRUCTION}",
                    "criteria": {
                        "true": "The user message requires calling this tool.",
                        "false": "The user message can be handled without this tool.",
                    },
                }
            else:
                questions[unit.key] = {
                    "type": "noul",
                    "instructions": f"units with id {unit.key}: {_INSTRUCTION}",
                    "criteria": {
                        "true": "The answer depends on this unit.",
                        "false": "The unit can be omitted without changing a correct answer.",
                    },
                }
        response = await self.client.post(
            f"{self.base_url}/v1/systemone",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": self.model, "state": state, "questions": questions},
            timeout=30,
        )
        if response.status_code >= 400:
            raise JevError(f"Jev returned {response.status_code}")
        body = response.json()
        answers = body.get("answers") or {}
        probabilities: dict[str, float] = {}
        for unit in units:
            answer = answers.get(unit.key) or {}
            value = answer.get("noul")
            if isinstance(value, (int, float)):
                probabilities[unit.key] = float(value)
        usage = body.get("usage") or {}
        return JevScores(
            probabilities=probabilities,
            input_tokens=int(usage.get("input_tokens") or 0),
            model=str(body.get("model") or self.model),
        )

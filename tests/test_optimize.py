import asyncio
import json

from inference_gateway.optimize import optimize_payload
from inference_gateway.units import apply_drops, plan_payload


class FakeJev:
    def __init__(self, scores):
        self.scores = scores

    async def score_batch(self, request_text, units):
        from inference_gateway.jev import JevScores

        return JevScores(
            probabilities={unit.key: self.scores.get(unit.key, 0.99) for unit in units},
            input_tokens=100,
            model="jev-test",
        )


THRESHOLDS = {"levels": {"low": 0.08, "normal": 0.35, "high": 0.62}}


def payload():
    return {
        "model": "gpt-5",
        "instructions": "You are a coding agent.",
        "input": [
            {"role": "user", "content": "What is the weather?"},
            {"role": "assistant", "content": "It is sunny."},
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "lookup",
                "arguments": "{}",
            },
            {"type": "function_call_output", "call_id": "call_1", "output": "sunny"},
            {"role": "user", "content": "Rename foo to bar in app.py"},
        ],
        "tools": [
            {"type": "function", "name": "lookup", "description": "weather"},
            {"type": "function", "name": "shell", "description": "run commands"},
        ],
    }


def test_current_request_and_instructions_stay():
    plan = plan_payload(payload())
    assert "Rename foo to bar" in plan.current_request
    assert "coding agent" in plan.current_request
    indexes = {i for unit in plan.candidates for i in unit.input_indexes}
    assert 4 not in indexes
    assert all(i < 4 for i in indexes)


def test_drops_only_below_threshold_and_preserves_bytes():
    body = payload()
    plan = plan_payload(body)
    scores = {unit.key: 0.1 for unit in plan.candidates}
    shell = next(unit for unit in plan.candidates if unit.tool_index == 1)
    scores[shell.key] = 0.99
    relevant = next(unit for unit in plan.candidates if unit.kind != "tool")
    scores[relevant.key] = 0.9
    result = asyncio.run(optimize_payload(
        body,
        level="normal",
        thresholds=THRESHOLDS,
        jev=FakeJev(scores),
        batch_size=4,
        concurrency=2,
    ))
    assert result.payload["instructions"] == body["instructions"]
    assert any(item == body["input"][relevant.input_indexes[0]] for item in result.payload["input"])
    assert body["input"][-1] in result.payload["input"]
    assert len(result.payload["input"]) < len(body["input"])
    assert result.payload["tools"] == [body["tools"][1]]
    assert result.optimized_tokens < result.original_tokens


def test_low_keeps_uncertain_units():
    body = payload()
    plan = plan_payload(body)
    scores = {unit.key: 0.2 for unit in plan.candidates}
    result = asyncio.run(optimize_payload(
        body,
        level="low",
        thresholds=THRESHOLDS,
        jev=FakeJev(scores),
        batch_size=8,
        concurrency=1,
    ))
    assert result.payload["input"] == body["input"]
    assert result.payload["tools"] == body["tools"]


def test_jev_failure_forwards_original():
    class Boom:
        async def score_batch(self, request_text, units):
            raise RuntimeError("down")

    body = payload()
    result = asyncio.run(optimize_payload(
        body,
        level="high",
        thresholds=THRESHOLDS,
        jev=Boom(),
        batch_size=4,
        concurrency=1,
    ))
    assert result.payload is body


def test_tool_used_in_current_turn_is_not_scored():
    body = payload()
    body["input"].extend(
        [
            {"type": "function_call", "call_id": "call_2", "name": "shell", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_2", "output": "ok"},
        ]
    )
    plan = plan_payload(body)
    assert all(unit.tool_index != 1 for unit in plan.candidates)
    dropped = apply_drops(body, {unit.key for unit in plan.candidates}, plan)
    names = [tool["name"] for tool in dropped["tools"]]
    assert names == ["shell"]


def test_additional_tools_stay_in_the_request():
    shell_leaf = {"type": "custom", "name": "shell", "description": "run commands"}
    weather_leaf = {"type": "custom", "name": "weather", "description": "forecast"}
    body = {
        "model": "gpt-5",
        "input": [
            {
                "type": "additional_tools",
                "role": "developer",
                "tools": [{"type": "namespace", "name": "functions", "description": "", "tools": [weather_leaf, shell_leaf]}],
            },
            {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "You are Codex."}]},
            {"role": "user", "content": "rename foo"},
        ],
    }
    plan = plan_payload(body)
    assert all(unit.kind != "tool" for unit in plan.candidates)
    result = asyncio.run(optimize_payload(
        body,
        level="normal",
        thresholds=THRESHOLDS,
        jev=FakeJev({}),
        batch_size=4,
        concurrency=1,
    ))
    leaves = result.payload["input"][0]["tools"][0]["tools"]
    assert leaves == [weather_leaf, shell_leaf]


def test_tools_use_the_configured_absolute_cutoff():
    body = payload()
    plan = plan_payload(body)
    scores = {unit.key: 0.50 for unit in plan.candidates}
    lookup = next(unit for unit in plan.candidates if unit.tool_index == 0)
    scores[lookup.key] = 0.01
    result = asyncio.run(optimize_payload(
        body,
        level="normal",
        thresholds=THRESHOLDS,
        jev=FakeJev(scores),
        batch_size=8,
        concurrency=1,
    ))
    names = [tool["name"] for tool in result.payload["tools"]]
    assert "shell" in names
    assert "lookup" not in names


def test_missing_or_invalid_scores_are_kept():
    body = payload()
    plan = plan_payload(body)
    scores = {plan.candidates[0].key: -1, plan.candidates[1].key: 2}
    result = asyncio.run(optimize_payload(
        body,
        level="high",
        thresholds=THRESHOLDS,
        jev=FakeJev(scores),
        batch_size=1,
        concurrency=2,
    ))
    # FakeJev fills unspecified scores with .99; the two invalid values are also kept.
    assert result.payload == body


def test_threshold_levels_are_increasingly_aggressive():
    body = payload()
    plan = plan_payload(body)
    scores = {unit.key: 0.20 for unit in plan.candidates}
    low = asyncio.run(optimize_payload(body, level="low", thresholds=THRESHOLDS, jev=FakeJev(scores), batch_size=8, concurrency=1))
    normal = asyncio.run(optimize_payload(body, level="normal", thresholds=THRESHOLDS, jev=FakeJev(scores), batch_size=8, concurrency=1))
    assert low.payload == body
    assert normal.optimized_tokens < low.optimized_tokens


def test_chat_title_uses_typed_user_text():
    from inference_gateway.units import chat_from_payload

    payload = {
        "input": [
            {
                "role": "user",
                "internal_chat_message_metadata_passthrough": {"content_item_kinds": ["plugins.recommendations"]},
                "content": [{"type": "input_text", "text": "<recommended_plugins>\nDropbox"}],
            },
            {
                "role": "user",
                "internal_chat_message_metadata_passthrough": {"content_item_kinds": ["user.text"]},
                "content": [{"type": "input_text", "text": "im thinking of applying to unis"}],
            },
        ]
    }
    _, title = chat_from_payload(payload)
    assert title == "im thinking of applying to unis"


def test_injected_user_metadata_does_not_replace_the_real_current_prompt():
    body = {
        "input": [
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
            {
                "role": "user",
                "internal_chat_message_metadata_passthrough": {"content_item_kinds": ["user.text"]},
                "content": [{"type": "input_text", "text": "fix the parser"}],
            },
            {
                "role": "user",
                "internal_chat_message_metadata_passthrough": {"content_item_kinds": ["plugins.recommendations"]},
                "content": [{"type": "input_text", "text": "<recommended_plugins>"}],
            },
        ]
    }
    plan = plan_payload(body)
    assert "fix the parser" in plan.user_message
    candidate_indexes = {index for unit in plan.candidates for index in unit.input_indexes}
    assert 2 not in candidate_indexes
    assert 3 not in candidate_indexes


def test_string_input_is_left_intact():
    body = {"model": "gpt-5", "input": "just this", "tools": []}
    plan = plan_payload(body)
    assert plan.candidates == []
    assert json.dumps(apply_drops(body, set(), plan)) == json.dumps(body)

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class Unit:
    key: str
    kind: str
    text: str
    input_indexes: list[int] = field(default_factory=list)
    tool_index: int | None = None
    catalog_path: tuple[int, ...] | None = None


@dataclass
class Plan:
    current_request: str
    user_message: str
    candidates: list[Unit]
    mandatory_input: set[int]
    mandatory_tools: set[int]


def _text_of(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for part in value:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                if isinstance(part.get("text"), str):
                    parts.append(part["text"])
                elif isinstance(part.get("content"), str):
                    parts.append(part["content"])
                else:
                    parts.append(json.dumps(part, ensure_ascii=False))
            else:
                parts.append(str(part))
        return "\n".join(parts)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _role(item: dict) -> str | None:
    role = item.get("role")
    return role if isinstance(role, str) else None


def _is_user(item: dict) -> bool:
    return _role(item) == "user"


def _is_actual_user(item: dict) -> bool:
    if not _is_user(item):
        return False
    meta = item.get("internal_chat_message_metadata_passthrough")
    if not isinstance(meta, dict):
        return True
    kinds = meta.get("content_item_kinds")
    return not isinstance(kinds, list) or not kinds or "user.text" in kinds


def _is_catalog(item: dict) -> bool:
    return item.get("type") == "additional_tools"


def _is_instruction(item: dict) -> bool:
    if _is_catalog(item):
        return False
    return _role(item) in {"system", "developer"}


def _call_id(item: dict) -> str | None:
    if item.get("type") not in {"function_call", "function_call_output", "custom_tool_call", "custom_tool_call_output"}:
        return None
    cid = item.get("call_id")
    return cid if isinstance(cid, str) else None


def _tool_name(tool: dict) -> str | None:
    name = tool.get("name")
    if isinstance(name, str):
        return name
    fn = tool.get("function")
    if isinstance(fn, dict) and isinstance(fn.get("name"), str):
        return fn["name"]
    return None


def _message_title(item: dict) -> str:
    meta = item.get("internal_chat_message_metadata_passthrough") or {}
    kinds = meta.get("content_item_kinds") or []
    if kinds and "user.text" not in kinds:
        return ""
    text = _text_of(item.get("content", "")).strip()
    marker = "User prompt:\n"
    if marker in text and text.startswith("You are a helpful assistant"):
        text = text.split(marker, 1)[1].strip()
    if not text or text.startswith("<"):
        return ""
    line = " ".join(text.split())
    limit = 48
    if len(line) <= limit:
        return line
    return line[: limit - 1].rstrip() + "…"


def chat_from_payload(payload: dict) -> tuple[str, str]:
    items = payload.get("input") if isinstance(payload.get("input"), list) else []
    chat_id = ""
    title = ""
    for item in items:
        if not isinstance(item, dict):
            continue
        meta = item.get("internal_chat_message_metadata_passthrough") or {}
        kinds = meta.get("content_item_kinds") or []
        if "model.base_instructions" in kinds and isinstance(item.get("id"), str):
            chat_id = item["id"]
        if item.get("role") == "user" and not title:
            title = _message_title(item)
    if not chat_id:
        for item in items:
            if isinstance(item, dict) and item.get("role") == "user" and isinstance(item.get("id"), str):
                chat_id = item["id"]
                break
    return chat_id, title


def plan_payload(payload: dict) -> Plan:
    instructions = payload.get("instructions")
    current_parts: list[str] = []
    if isinstance(instructions, str) and instructions.strip():
        current_parts.append(instructions.strip())

    raw_input = payload.get("input")
    user_message = ""
    items: list[dict] | None
    if isinstance(raw_input, list):
        items = [item for item in raw_input if isinstance(item, dict)]
        if len(items) != len(raw_input):
            return Plan(current_request="\n\n".join(current_parts), user_message="", candidates=[], mandatory_input=set(), mandatory_tools=set())
    else:
        items = None
        if isinstance(raw_input, str):
            current_parts.append(raw_input)
            user_message = raw_input.strip()

    mandatory_input: set[int] = set()
    if items is not None:
        user_indexes = [i for i, item in enumerate(items) if _is_actual_user(item)]
        if not user_indexes:
            user_indexes = [i for i, item in enumerate(items) if _is_user(item)]
        if user_indexes:
            start = user_indexes[-1]
            mandatory_input.update(range(start, len(items)))
            current_parts.append(_text_of(items[start].get("content", items[start])).strip())
            for item in reversed(items):
                if isinstance(item, dict) and _is_actual_user(item):
                    body = _message_title(item)
                    if body:
                        user_message = _text_of(item.get("content", "")).strip()
                        marker = "User prompt:\n"
                        if marker in user_message and user_message.startswith("You are a helpful assistant"):
                            user_message = user_message.split(marker, 1)[1].strip()
                        break
        for i, item in enumerate(items):
            if _is_catalog(item):
                mandatory_input.add(i)
                continue
            if _is_instruction(item):
                mandatory_input.add(i)
                current_parts.append(_text_of(item.get("content", item)))

        by_call: dict[str, list[int]] = {}
        for i, item in enumerate(items):
            cid = _call_id(item)
            if cid:
                by_call.setdefault(cid, []).append(i)
        for indexes in by_call.values():
            if any(i in mandatory_input for i in indexes):
                mandatory_input.update(indexes)

    tools = payload.get("tools") if isinstance(payload.get("tools"), list) else []
    used_names: set[str] = set()
    if items is not None:
        for i, item in enumerate(items):
            if i not in mandatory_input:
                continue
            if item.get("type") in {"function_call", "custom_tool_call"} and isinstance(item.get("name"), str):
                used_names.add(item["name"])

    mandatory_tools: set[int] = set()
    for i, tool in enumerate(tools):
        if isinstance(tool, dict) and _tool_name(tool) in used_names:
            mandatory_tools.add(i)

    candidates: list[Unit] = []
    if items is not None:
        grouped: set[int] = set()
        by_call_candidates: dict[str, list[int]] = {}
        for i, item in enumerate(items):
            if i in mandatory_input:
                continue
            cid = _call_id(item)
            if cid:
                by_call_candidates.setdefault(cid, []).append(i)
        n = 0
        for cid, indexes in by_call_candidates.items():
            grouped.update(indexes)
            blob = items[indexes[0]]
            name = blob.get("name", cid)
            output = ""
            for i in indexes:
                if items[i].get("type") in {"function_call_output", "custom_tool_call_output"}:
                    output = _text_of(items[i].get("output"))
            candidates.append(
                Unit(
                    key=f"h{n}",
                    kind="tool_result",
                    text=f"tool call {name}\n{output}",
                    input_indexes=indexes,
                )
            )
            n += 1
        for i, item in enumerate(items):
            if _is_catalog(item) or i in mandatory_input or i in grouped:
                continue
            kind = "history"
            if item.get("type") in {"reasoning"}:
                kind = "other"
            candidates.append(
                Unit(
                    key=f"h{n}",
                    kind=kind,
                    text=_text_of(item.get("content", item)),
                    input_indexes=[i],
                )
            )
            n += 1

    for i, tool in enumerate(tools):
        if not isinstance(tool, dict) or i in mandatory_tools:
            continue
        candidates.append(
            Unit(
                key=f"t{i}",
                kind="tool",
                text=json.dumps(tool, ensure_ascii=False),
                tool_index=i,
            )
        )

    return Plan(
        current_request="\n\n".join(part for part in current_parts if part),
        user_message=user_message,
        candidates=candidates,
        mandatory_input=mandatory_input,
        mandatory_tools=mandatory_tools,
    )


def _catalog_leaves(nodes: list, prefix: tuple[int, ...] = ()) -> list[tuple[tuple[int, ...], dict]]:
    leaves = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        path = prefix + (index,)
        children = node.get("tools")
        if isinstance(children, list):
            leaves.extend(_catalog_leaves(children, path))
        else:
            leaves.append((path, node))
    return leaves


def _filter_catalog(nodes: list, dropped: set[tuple[int, ...]], prefix: tuple[int, ...] = ()) -> list:
    kept = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            kept.append(node)
            continue
        path = prefix + (index,)
        children = node.get("tools")
        if isinstance(children, list):
            filtered = _filter_catalog(children, dropped, path)
            if not filtered:
                continue
            copied = dict(node)
            copied["tools"] = filtered
            kept.append(copied)
        elif path not in dropped:
            kept.append(node)
    return kept


def apply_drops(payload: dict, dropped_keys: set[str], plan: Plan) -> dict:
    if not dropped_keys:
        return payload
    drop_input: set[int] = set()
    drop_tools: set[int] = set()
    catalog_drops: dict[int, set[tuple[int, ...]]] = {}
    for unit in plan.candidates:
        if unit.key not in dropped_keys:
            continue
        if unit.catalog_path is not None and unit.input_indexes:
            catalog_drops.setdefault(unit.input_indexes[0], set()).add(unit.catalog_path)
            continue
        drop_input.update(unit.input_indexes)
        if unit.tool_index is not None:
            drop_tools.add(unit.tool_index)

    updated = dict(payload)
    raw_input = payload.get("input")
    if isinstance(raw_input, list) and (drop_input or catalog_drops):
        rebuilt = []
        for index, item in enumerate(raw_input):
            if index in drop_input:
                continue
            if index in catalog_drops and isinstance(item, dict):
                tools = _filter_catalog(item.get("tools") or [], catalog_drops[index])
                if not tools:
                    continue
                copied = dict(item)
                copied["tools"] = tools
                rebuilt.append(copied)
            else:
                rebuilt.append(item)
        updated["input"] = rebuilt
    tools = payload.get("tools")
    if isinstance(tools, list) and drop_tools:
        updated["tools"] = [tool for i, tool in enumerate(tools) if i not in drop_tools]
    return updated

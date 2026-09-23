from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from inference_gateway.config import data_dir

STATE_FILE = "routing.json"
_KEY = re.compile(r"(?m)^(?P<indent>[ \t]*)openai_base_url[ \t]*=[ \t]*(?P<value>[^\r\n#]*)(?P<tail>[ \t]*(?:#.*)?)$")


def _top_level_end(text: str) -> int:
    match = re.search(r"(?m)^[ \t]*\[", text)
    return match.start() if match else len(text)


def _top_level_key(text: str):
    return _KEY.search(text, 0, _top_level_end(text))


def codex_config_path() -> Path:
    root = os.environ.get("CODEX_HOME", "").strip()
    return (Path(root).expanduser() if root else Path.home() / ".codex") / "config.toml"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def enable_codex(base_url: str, *, path: Path | None = None) -> None:
    """Point Codex at the gateway and remember only the setting we replaced."""
    target = path or codex_config_path()
    original = target.read_text(encoding="utf-8") if target.exists() else ""
    match = _top_level_key(original)
    state = {
        "path": str(target),
        "had_key": bool(match),
        "original_line": match.group(0) if match else None,
        "gateway_value": base_url.rstrip("/"),
    }
    replacement = f'openai_base_url = "{base_url.rstrip("/")}"'
    if match:
        updated = original[: match.start()] + replacement + original[match.end() :]
    else:
        split = _top_level_end(original)
        before, after = original[:split], original[split:]
        prefix = "" if not before or before.endswith(("\n", "\r")) else "\n"
        updated = before + prefix + replacement + "\n" + after
    _atomic_write(target, updated)
    _atomic_write(data_dir() / STATE_FILE, json.dumps(state, indent=2) + "\n")


def disable_codex(*, path: Path | None = None) -> bool:
    """Restore routing without overwriting a change the user made meanwhile."""
    state_path = data_dir() / STATE_FILE
    if not state_path.exists():
        return False
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    target = path or Path(state.get("path") or codex_config_path())
    if not target.exists():
        state_path.unlink(missing_ok=True)
        return False
    text = target.read_text(encoding="utf-8")
    match = _top_level_key(text)
    expected = str(state.get("gateway_value") or "").rstrip("/")
    if not match or match.group("value").strip().strip('"').strip("'").rstrip("/") != expected:
        return False
    if state.get("had_key") and isinstance(state.get("original_line"), str):
        updated = text[: match.start()] + state["original_line"] + text[match.end() :]
    else:
        start, end = match.span()
        if end < len(text) and text[end : end + 2] == "\r\n":
            end += 2
        elif end < len(text) and text[end] in "\r\n":
            end += 1
        updated = text[:start] + text[end:]
    _atomic_write(target, updated)
    state_path.unlink(missing_ok=True)
    return True


def routing_status(*, path: Path | None = None) -> str | None:
    target = path or codex_config_path()
    if not target.exists():
        return None
    match = _top_level_key(target.read_text(encoding="utf-8"))
    return match.group("value").strip().strip('"').strip("'") if match else None


def ensure_codex(base_url: str, *, path: Path | None = None) -> bool:
    """Apply the managed route when it is missing or points elsewhere."""
    expected = base_url.rstrip("/")
    current = (routing_status(path=path) or "").rstrip("/")
    if current == expected:
        return False
    enable_codex(expected, path=path)
    return True

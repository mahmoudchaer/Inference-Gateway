from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
PROJECT = PACKAGE.parent.parent
CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "optimization": "normal",
    "logs_enabled": True,
    "host": "127.0.0.1",
    "port": 8080,
    "upstream": "https://chatgpt.com/backend-api/codex",
    "jev_model": "jev-latest",
    "jev_base_url": "https://api.typesafe.ai",
    "batch_size": 6,
    "jev_concurrency": 4,
}


def load_dotenv() -> None:
    """Load a development .env or the saved Jev key. Environment wins."""
    if os.environ.get("TYPESAFE_API_KEY", "").strip():
        return
    for path in (Path.cwd() / ".env", PROJECT / ".env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() == "TYPESAFE_API_KEY":
                os.environ["TYPESAFE_API_KEY"] = value.strip().strip('"').strip("'")
                return
    saved = read_config(include_secret=True)
    if saved.get("jev_api_key"):
        os.environ["TYPESAFE_API_KEY"] = str(saved["jev_api_key"])


def data_dir() -> Path:
    raw = os.environ.get("GATEWAY_DATA_DIR", "").strip()
    path = Path(raw).expanduser() if raw else Path.home() / ".inference-gateway"
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_config(*, include_secret: bool = False) -> dict:
    config = dict(DEFAULT_CONFIG)
    path = data_dir() / CONFIG_FILE
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                config.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass
    if not include_secret:
        config.pop("jev_api_key", None)
    return config


def write_config(updates: dict) -> dict:
    """Persist validated user settings with owner-only permissions."""
    allowed = set(DEFAULT_CONFIG) | {"jev_api_key"}
    current = read_config(include_secret=True)
    current.update({key: value for key, value in updates.items() if key in allowed})
    level = str(current.get("optimization", "normal")).lower()
    current["optimization"] = level if level in {"low", "normal", "high"} else "normal"
    current["logs_enabled"] = bool(current.get("logs_enabled", True))
    current["host"] = str(current.get("host") or "127.0.0.1")
    current["port"] = min(65535, max(1, int(current.get("port", 8080))))
    current["batch_size"] = max(1, int(current.get("batch_size", 6)))
    current["jev_concurrency"] = max(1, int(current.get("jev_concurrency", 4)))
    destination = data_dir() / CONFIG_FILE
    fd, temporary = tempfile.mkstemp(prefix="config-", suffix=".json", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            if hasattr(os, "fchmod"):
                os.fchmod(handle.fileno(), 0o600)
            json.dump(current, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, destination)
        destination.chmod(0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return current


def load_thresholds() -> dict:
    override = os.environ.get("GATEWAY_THRESHOLDS", "").strip()
    path = Path(override).expanduser() if override else data_dir() / "thresholds.json"
    if not path.exists():
        path = PACKAGE / "thresholds.json"
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    upstream: str
    optimization: str
    metrics_enabled: bool
    jev_model: str
    jev_base_url: str
    batch_size: int
    jev_concurrency: int

    @classmethod
    def from_env(cls) -> Settings:
        saved = read_config(include_secret=True)
        if not os.environ.get("TYPESAFE_API_KEY", "").strip() and saved.get("jev_api_key"):
            os.environ["TYPESAFE_API_KEY"] = str(saved["jev_api_key"])
        level = os.environ.get("GATEWAY_OPTIMIZATION", str(saved["optimization"])).strip().lower()
        if level not in {"low", "normal", "high"}:
            level = "normal"
        metrics_default = "1" if saved["logs_enabled"] else "0"
        metrics = os.environ.get("GATEWAY_METRICS", metrics_default).strip().lower() not in {"0", "false", "off", "no"}
        upstream = os.environ.get("GATEWAY_UPSTREAM", str(saved["upstream"])).rstrip("/")
        return cls(
            host=os.environ.get("GATEWAY_HOST", str(saved["host"])),
            port=int(os.environ.get("GATEWAY_PORT", str(saved["port"]))),
            upstream=upstream,
            optimization=level,
            metrics_enabled=metrics,
            jev_model=os.environ.get("GATEWAY_JEV_MODEL", str(saved["jev_model"])).strip(),
            jev_base_url=os.environ.get("TYPESAFE_BASE_URL", str(saved["jev_base_url"])).rstrip("/"),
            batch_size=max(1, int(os.environ.get("GATEWAY_JEV_BATCH", str(saved["batch_size"])))),
            jev_concurrency=max(1, int(os.environ.get("GATEWAY_JEV_CONCURRENCY", str(saved["jev_concurrency"])))),
        )

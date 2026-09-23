from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

from inference_gateway.app import create_app
from inference_gateway.cli_ui import banner, dim, good, help_text, row, say, warn
from inference_gateway.config import (
    Settings,
    data_dir,
    load_dotenv,
    read_config,
    write_config,
)
from inference_gateway.routing import (
    disable_codex,
    enable_codex,
    ensure_codex,
    routing_status,
)
from inference_gateway.uninstall import uninstall
from inference_gateway.update import update

PID_FILE = "gateway.pid"
LOG_FILE = "gateway.log"


def _pid_path() -> Path:
    return data_dir() / PID_FILE


def _read_pid() -> int | None:
    try:
        return int(_pid_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _url(settings: Settings) -> str:
    return f"http://{settings.host}:{settings.port}"


def _terminal_link(url: str, interactive: bool | None = None) -> str:
    if interactive is None:
        interactive = sys.stdout.isatty() and os.environ.get("TERM", "") != "dumb"
    return f"\033]8;;{url}\033\\{url}\033]8;;\033\\" if interactive else url


def _interrupt_on_hangup(_signum, _frame) -> None:
    raise KeyboardInterrupt


def _serve(open_browser: bool = False) -> None:
    load_dotenv()
    settings = Settings.from_env()
    if settings.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("Refusing to expose request logs off-device. Use a loopback host.")
    # Recover a route left behind by an interrupted earlier run before taking a
    # fresh snapshot. Otherwise the gateway URL could become its own backup.
    disable_codex()
    enable_codex(f"{_url(settings)}/v1")
    previous_hangup = None
    if hasattr(signal, "SIGHUP"):
        previous_hangup = signal.getsignal(signal.SIGHUP)
        signal.signal(signal.SIGHUP, _interrupt_on_hangup)
    try:
        _pid_path().write_text(str(os.getpid()) + "\n", encoding="utf-8")
        if open_browser:
            webbrowser.open(_url(settings))
        print(banner())
        say(f"{good('●')} running  {_terminal_link(_url(settings))}")
        say(dim("Press ctrl-c to stop."))
        print()
        uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="warning")
    finally:
        if previous_hangup is not None:
            signal.signal(signal.SIGHUP, previous_hangup)
        _pid_path().unlink(missing_ok=True)
        disable_codex()
        print()
        say(dim("○ stopped"))
        say(dim("Codex routing restored."))


def _wait_ready(url: str, process: subprocess.Popen, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=0.25) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.1)
    return False


def _start_background(open_browser: bool) -> None:
    pid = _read_pid()
    if _alive(pid):
        print(banner())
        say(good("● already running"))
        print(row("pid", str(pid)))
        return
    _pid_path().unlink(missing_ok=True)
    settings = Settings.from_env()
    log_path = data_dir() / LOG_FILE
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS if os.name == "nt" else 0
    process_options = {"creationflags": flags} if os.name == "nt" else {"start_new_session": True}
    with log_path.open("ab") as log:
        child_command = [sys.executable, "_serve"] if getattr(sys, "frozen", False) else [sys.executable, "-m", "inference_gateway", "_serve"]
        process = subprocess.Popen(
            child_command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            **process_options,
        )
    if not _wait_ready(_url(settings), process):
        disable_codex()
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-1200:].strip()
        except OSError:
            tail = ""
        raise SystemExit(f"Gateway did not start. See {log_path}.\n{tail}")
    if open_browser:
        webbrowser.open(_url(settings))
    print(banner())
    say(f"{good('●')} running  {_terminal_link(_url(settings))}")
    say(dim("Stop with inference-gateway stop."))


def _stop() -> None:
    pid = _read_pid()
    if _alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        deadline = time.monotonic() + 8
        while _alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
    restored = disable_codex()
    _pid_path().unlink(missing_ok=True)
    print(banner())
    say(dim("○ stopped"))
    say(dim("Codex routing restored.") if restored else dim("Codex was not pointing at this gateway."))


def _status() -> None:
    settings = Settings.from_env()
    pid = _read_pid()
    running = _alive(pid)
    route = routing_status()
    print(banner())
    say(good("● running") if running else dim("○ stopped"))
    if running:
        print(row("pid", str(pid)))
    print(row("dashboard", _terminal_link(_url(settings))))
    expected = f"{_url(settings)}/v1"
    if running and (route or "").rstrip("/") != expected.rstrip("/"):
        print(row("codex", warn("not connected — run inference-gateway start")))
    else:
        print(row("codex", route or dim("default route")))
    print(row("level", settings.optimization))
    print(row("logs", "on" if settings.metrics_enabled else "off"))
    key = good("configured") if os.environ.get("TYPESAFE_API_KEY") else warn("missing")
    print(row("jev key", key))


def _configure(args) -> None:
    updates = {}
    if args.level:
        updates["optimization"] = args.level
    if args.logs:
        updates["logs_enabled"] = args.logs == "on"
    if args.api_key is not None:
        updates["jev_api_key"] = args.api_key.strip()
    if not updates:
        print(json.dumps(read_config(), indent=2))
        return
    config = write_config(updates)
    suffix = " Restart the gateway to apply them." if _alive(_read_pid()) else ""
    print(banner())
    say(good("● settings saved") + dim(suffix))
    print(row("level", config["optimization"]))
    print(row("logs", "on" if config["logs_enabled"] else "off"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="inference-gateway",
        description="Use less Codex context without changing your prompts.",
        epilog="Start with: inference-gateway config --api-key YOUR_JEV_KEY && inference-gateway start",
    )
    sub = parser.add_subparsers(dest="command")
    start = sub.add_parser("start", help="run in this terminal until Ctrl-C")
    start.add_argument("--background", action="store_true", help="run detached in the background")
    start.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    run = sub.add_parser("run", help="alias for foreground start")
    run.add_argument("--open", action="store_true", help="open the dashboard")
    sub.add_parser("stop", help="stop a background instance and restore routing")
    sub.add_parser("status", help="show gateway and routing status")
    sub.add_parser("repair", help="restore Codex routing after an interrupted shutdown")
    sub.add_parser("update", help="download and install the latest release")
    sub.add_parser("uninstall", help="remove the CLI, settings, and logs")
    sub.add_parser("help", help="list commands and what each one does")
    config = sub.add_parser("config", help="view or change settings")
    config.add_argument("--level", choices=["low", "normal", "high"])
    config.add_argument("--logs", choices=["on", "off"])
    config.add_argument("--api-key", help="save a Jev API key locally; pass an empty value to clear it")
    return parser


def main() -> None:
    load_dotenv()
    if len(sys.argv) > 1 and sys.argv[1] == "_serve":
        _serve(False)
        return
    parser = _parser()
    args = parser.parse_args()
    if args.command == "start":
        if args.background:
            _start_background(args.open)
        elif _alive(_read_pid()):
            print(banner())
            settings = Settings.from_env()
            if ensure_codex(f"{_url(settings)}/v1"):
                say(good("● already running — Codex reconnected"))
            else:
                say(good("● already running"))
            print(row("pid", str(_read_pid())))
        else:
            try:
                _serve(args.open)
            except KeyboardInterrupt:
                pass
    elif args.command == "run":
        try:
            _serve(args.open)
        except KeyboardInterrupt:
            pass
    elif args.command == "stop":
        _stop()
    elif args.command == "status":
        _status()
    elif args.command == "repair":
        print(banner())
        say(good("● routing restored") if disable_codex() else dim("No managed routing change was found."))
    elif args.command == "update":
        if _alive(_read_pid()):
            say(dim("Stopping the gateway before updating..."))
            _stop()
        try:
            update()
        except Exception as error:
            raise SystemExit(f"Update failed: {error}") from error
    elif args.command == "uninstall":
        if _alive(_read_pid()):
            say(dim("Stopping the gateway before uninstalling..."))
            _stop()
        else:
            disable_codex()
        try:
            uninstall()
        except Exception as error:
            raise SystemExit(f"Uninstall failed: {error}") from error
    elif args.command == "help":
        print(help_text())
    elif args.command == "config":
        _configure(args)
    else:
        print(help_text())


if __name__ == "__main__":
    main()

from __future__ import annotations

import os
import sys

from inference_gateway import __version__


def _color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty() and os.environ.get("TERM", "") != "dumb"


def paint(text: str, code: str) -> str:
    if not _color_enabled():
        return text
    return f"\033[{code}m{text}\033[0m"


def accent(text: str) -> str:
    return paint(text, "38;5;75")


def dim(text: str) -> str:
    return paint(text, "2")


def good(text: str) -> str:
    return paint(text, "38;5;114")


def warn(text: str) -> str:
    return paint(text, "38;5;179")


def banner() -> str:
    title = accent("inference gateway")
    version = dim(f"v{__version__}")
    return f"\n  {title}  {version}\n"


def row(label: str, value: str) -> str:
    return f"  {label:<12}{value}"


def say(message: str) -> None:
    print(f"  {message}")


def help_text() -> str:
    commands = [
        ("start", "run in this terminal until Ctrl-C"),
        ("start --background", "run detached"),
        ("stop", "stop a background instance"),
        ("status", "show gateway and routing"),
        ("config", "view or change settings"),
        ("repair", "restore Codex routing"),
        ("update", "install the latest release"),
        ("uninstall", "remove the CLI, settings, and logs"),
    ]
    lines = [
        banner().rstrip("\n"),
        "",
        dim("  Use less Codex context without changing your prompts."),
        "",
        f"  {accent('commands')}",
    ]
    lines.extend(f"  {name:<20}{dim(description)}" for name, description in commands)
    lines.extend(
        [
            "",
            f"  {accent('start')}",
            dim("    inference-gateway config --api-key <key>"),
            dim("    inference-gateway start"),
            "",
        ]
    )
    return "\n".join(lines)

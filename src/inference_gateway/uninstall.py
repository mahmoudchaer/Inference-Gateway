from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from inference_gateway.cli_ui import dim, good, say
from inference_gateway.config import data_dir

PATH_MARKER = "# Added by Inference Gateway installer"
PATH_EXPORT = 'export PATH="$HOME/.local/bin:$PATH"'


def remove_profile_entry(profile: Path) -> None:
    if not profile.is_file():
        return
    lines = profile.read_text(encoding="utf-8").splitlines()
    cleaned: list[str] = []
    skip_export = False
    for line in lines:
        if line.strip() == PATH_MARKER:
            skip_export = True
            continue
        if skip_export and line.strip() == PATH_EXPORT:
            skip_export = False
            continue
        skip_export = False
        cleaned.append(line)
    text = "\n".join(cleaned).rstrip()
    profile.write_text(f"{text}\n" if text else "", encoding="utf-8")


def remove_user_data(path: Path | None = None) -> None:
    shutil.rmtree(path or data_dir(), ignore_errors=True)


def _schedule_windows_removal(target: Path) -> None:
    escaped_target = str(target).replace("'", "''")
    escaped_parent = str(target.parent).replace("'", "''")
    command = (
        f"Wait-Process -Id {os.getpid()} -ErrorAction SilentlyContinue; "
        f"Remove-Item -LiteralPath '{escaped_target}' -Force -ErrorAction SilentlyContinue; "
        f"Remove-Item -LiteralPath '{escaped_parent}' -Force -ErrorAction SilentlyContinue"
    )
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", command],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )


def _remove_windows_path(install_dir: Path) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
        try:
            current, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            return
        wanted = os.path.normcase(str(install_dir.resolve()))
        parts = [part for part in str(current).split(";") if part]
        filtered = [part for part in parts if os.path.normcase(str(Path(part).resolve())) != wanted]
        if filtered != parts:
            winreg.SetValueEx(key, "Path", 0, kind, ";".join(filtered))


def uninstall() -> None:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Uninstall is available in the installed standalone CLI only.")

    target = Path(sys.executable).resolve()
    remove_user_data()

    if os.name == "nt":
        _remove_windows_path(target.parent)
        _schedule_windows_removal(target)
        say(good("● uninstalled") + dim("  settings and logs were deleted"))
        return

    for name in (".zprofile", ".bash_profile"):
        remove_profile_entry(Path.home() / name)
    target.unlink()
    try:
        target.parent.rmdir()
    except OSError:
        pass
    say(good("● uninstalled") + dim("  settings and logs were deleted"))

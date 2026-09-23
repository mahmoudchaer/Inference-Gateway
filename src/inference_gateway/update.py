from __future__ import annotations

import hashlib
import json
import os
import platform
import ssl
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

import certifi

from inference_gateway import __version__
from inference_gateway.cli_ui import dim, good, say

REPOSITORY = "mahmoudchaer/Inference-Gateway"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"


def artifact_name(system: str | None = None, machine: str | None = None) -> str:
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    if system == "Darwin" and machine in {"arm64", "aarch64"}:
        return "inference-gateway-macos-arm64"
    if system == "Darwin" and machine in {"x86_64", "amd64"}:
        return "inference-gateway-macos-x64"
    if system == "Windows" and machine in {"x86_64", "amd64"}:
        return "inference-gateway-windows-x64.exe"
    raise RuntimeError(f"Updates are not available for {system} {machine}.")


def checksum_for(contents: str, artifact: str) -> str:
    for line in contents.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == artifact:
            return parts[0].lower()
    raise RuntimeError("The release checksum is missing.")


def version_key(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.lstrip("v").split("."))
    except ValueError as error:
        raise RuntimeError(f"Unsupported release version: {value}") from error


def _ssl_context() -> ssl.SSLContext:
    # Frozen Python builds cannot reliably discover the operating system's CA
    # bundle. certifi is packaged with the CLI, so HTTPS verification remains on.
    return ssl.create_default_context(cafile=certifi.where())


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": f"InferenceGateway/{__version__}"})
    with urllib.request.urlopen(request, timeout=30, context=_ssl_context()) as response:
        return response.read()


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": f"InferenceGateway/{__version__}"})
    with urllib.request.urlopen(request, timeout=60, context=_ssl_context()) as response, destination.open("wb") as output:
        total = int(response.headers.get("Content-Length", 0))
        received = 0
        while True:
            chunk = response.read(1024 * 256)
            if not chunk:
                break
            output.write(chunk)
            received += len(chunk)
            if total:
                width = 28
                filled = min(width, int(width * received / total))
                percent = min(100, int(100 * received / total))
                print(f"\r  {good('#' * filled)}{dim('-' * (width - filled))} {percent:3d}%", end="", flush=True)
            else:
                print(f"\r  Downloaded {received / (1024 * 1024):.1f} MB", end="", flush=True)
    print()


def _schedule_windows_replace(download: Path, target: Path) -> None:
    # A running Windows executable is locked. A detached PowerShell process waits
    # for this CLI to exit, swaps the verified file, then removes the temporary file.
    command = (
        f"Wait-Process -Id {os.getpid()} -ErrorAction SilentlyContinue; "
        f"Move-Item -LiteralPath {json.dumps(str(download))} "
        f"-Destination {json.dumps(str(target))} -Force"
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


def update() -> None:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Self-update is available in the installed standalone CLI only.")

    release = json.loads(_get(API_URL))
    latest = str(release["tag_name"]).lstrip("v")
    if version_key(latest) <= version_key(__version__):
        say(good("● up to date") + dim(f"  v{__version__}"))
        return

    artifact = artifact_name()
    assets = {asset["name"]: asset["browser_download_url"] for asset in release.get("assets", [])}
    if artifact not in assets or "checksums.txt" not in assets:
        raise RuntimeError("The latest release does not contain a build for this computer.")

    say(dim(f"Updating v{__version__} → v{latest}"))
    target = Path(sys.executable).resolve()
    descriptor, download_name = tempfile.mkstemp(prefix="inference-gateway-", suffix=target.suffix)
    os.close(descriptor)
    download: Path | None = Path(download_name)
    try:
        _download(assets[artifact], download)
        expected = checksum_for(_get(assets["checksums.txt"]).decode("utf-8"), artifact)
        actual = hashlib.sha256(download.read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError("Checksum verification failed; the installed version was not changed.")
        download.chmod(0o755)
        if os.name == "nt":
            _schedule_windows_replace(download, target)
            download = None
            say(good("● downloaded") + dim(f"  v{latest} will finish installing now"))
        else:
            os.replace(download, target)
            download = None
            say(good("● updated") + dim(f"  v{latest}"))
    finally:
        if download is not None:
            download.unlink(missing_ok=True)

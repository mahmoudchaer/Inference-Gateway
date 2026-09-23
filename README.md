# Inference Gateway

[![CI](https://github.com/mahmoudchaer/Inference-Gateway/actions/workflows/build.yml/badge.svg)](https://github.com/mahmoudchaer/Inference-Gateway/actions/workflows/build.yml)
[![Security](https://github.com/mahmoudchaer/Inference-Gateway/actions/workflows/security.yml/badge.svg)](https://github.com/mahmoudchaer/Inference-Gateway/actions/workflows/security.yml)
[![Release](https://img.shields.io/github/v/release/mahmoudchaer/Inference-Gateway)](https://github.com/mahmoudchaer/Inference-Gateway/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Use less Codex context without changing how you work. Inference Gateway runs locally, identifies context that is not needed for the current request, and forwards a smaller request to Codex.

> [!IMPORTANT]
> This project is an early public release. The optimization cutoffs are provisional; evaluate task correctness as well as token savings before relying on aggressive settings.

- Local-only gateway and dashboard
- Safe pass-through if Jev is unavailable or uncertain
- Automatic Codex routing setup and restoration
- Native commands for macOS Apple Silicon, macOS Intel, and Windows x64
- Built-in update and complete uninstall

## Quick start

### macOS

Install and refresh the same terminal automatically:

```sh
curl -fsSL https://raw.githubusercontent.com/mahmoudchaer/Inference-Gateway/main/install.sh | sh && exec "$SHELL" -l
```

### Windows PowerShell

```powershell
irm https://raw.githubusercontent.com/mahmoudchaer/Inference-Gateway/main/install.ps1 | iex
```

Then configure and start:

```sh
inference-gateway config --api-key YOUR_JEV_KEY
inference-gateway start
```

`start` keeps the gateway attached to the terminal and prints a clickable dashboard link. Press Ctrl-C or close the terminal to stop it and restore Codex routing. It does not open a browser automatically. Installation itself does not start the gateway or change routing. Python is not required.

## Commands

Run `inference-gateway help` or `inference-gateway --help` at any time.

| Command | What it does |
| --- | --- |
| `inference-gateway start` | Runs in the current terminal until Ctrl-C and prints the dashboard link. |
| `inference-gateway start --open` | Runs in the current terminal and also opens the dashboard. |
| `inference-gateway start --background` | Explicitly starts a detached background instance. |
| `inference-gateway stop` | Stops a background instance and restores the previous Codex route. |
| `inference-gateway status` | Shows whether it is running, its URL, optimization level, logs, and key status. |
| `inference-gateway config` | Shows current settings. |
| `inference-gateway config --api-key KEY` | Saves the Jev API key locally. |
| `inference-gateway config --level low\|normal\|high` | Changes optimization strength. |
| `inference-gateway config --logs on\|off` | Enables or disables request logging. |
| `inference-gateway update` | Downloads and verifies the newest tagged release. |
| `inference-gateway repair` | Restores Codex routing after an interrupted shutdown. |
| `inference-gateway uninstall` | Stops everything and removes the CLI, settings, API key, and logs. |
| `inference-gateway run` | Alias for foreground `start`. |

## How it works

Jev scores old messages, completed tool-call pairs, and unused tool definitions against the current request. The gateway removes only candidates below the selected probability cutoff; it never rewrites messages. If Jev is unavailable or returns an incomplete result, the original request passes through unchanged.

The gateway binds only to the local device. Codex keeps its own authentication. `start` saves the existing Codex route before changing it. Ctrl-C, `stop`, `repair`, and `uninstall` restore that route safely. It will not overwrite a route the user changed afterward.

Settings are also available in the dashboard. A restart is currently required after changing them.

## Optimization levels

Jev returns the probability that each candidate is required. Candidates below the selected absolute cutoff are removed:

- `low`: conservative; removes only very clearly irrelevant context.
- `normal`: balanced default.
- `high`: more aggressive; saves more tokens with more risk.

The included cutoffs are provisional. `python -m inference_gateway.calibrate` runs labeled fixtures with your configured Jev model and writes device-specific cutoffs. Real-world evaluation should measure both token reduction and task correctness before changing the defaults.

## Privacy and files

By default, the SQLite log contains original/forwarded context and captured model output so the dashboard can explain each optimization. Turn logs off if prompts are sensitive. The dashboard is restricted to a loopback address.

Data locations:

- macOS/Linux: `~/.inference-gateway`
- Windows: `%USERPROFILE%\.inference-gateway`

`inference-gateway uninstall` stops the gateway, restores Codex routing, and removes the CLI together with all saved settings and logs.

The Jev key is stored in a user-only configuration file. `TYPESAFE_API_KEY` and a project `.env` remain supported for development.

## Development

Python 3.11 or newer is required:

```text
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

On Windows, use `.venv\Scripts\python` and `.venv\Scripts\pip`.

Tests use local fake upstreams and fake Jev responses; they do not start the real proxy or alter the real Codex configuration. Build a standalone executable with:

```text
pyinstaller --clean --noconfirm inference-gateway.spec
```

Tagged releases can use the included workflow to produce Apple Silicon, Intel macOS, and Windows x64 binaries.

## Current scope

Codex is the supported client. The proxy core is client-neutral, but Claude Code, Cursor, and other request formats should be added as explicit adapters with captured fixtures rather than assumed to be wire-compatible.

## Project documentation

- [Architecture and safety model](docs/ARCHITECTURE.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Support](SUPPORT.md)
- [Changelog](CHANGELOG.md)

Inference Gateway is independently developed and is not affiliated with or endorsed by OpenAI. Codex and OpenAI are trademarks of their respective owners.

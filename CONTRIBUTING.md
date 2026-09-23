# Contributing

Thanks for helping improve Inference Gateway.

## Before opening an issue

- Search existing issues first.
- Use the bug or feature template and include the operating system and gateway version.
- Never post API keys, prompts, source code, request logs, or other sensitive data.
- Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

## Development setup

Python 3.11 or newer is required.

```sh
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check .
.venv/bin/python -m pytest
```

On Windows, use `.venv\Scripts\python`, `.venv\Scripts\pip`, and `.venv\Scripts\ruff`.

Tests must not start the real gateway, modify the user's Codex configuration, or call paid external APIs. Use temporary files and fake upstreams.

## Pull requests

Keep changes focused, add or update tests for behavior changes, and update documentation when commands or user-facing behavior changes. All CI checks must pass. By contributing, you agree that your contribution is licensed under the MIT License.

Only maintainers create release tags. Versioned releases must be built by the repository workflow rather than uploading local binaries.

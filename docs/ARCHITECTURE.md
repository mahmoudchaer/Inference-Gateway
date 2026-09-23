# Architecture and safety model

Inference Gateway is a loopback-only HTTP proxy between Codex and its normal upstream. It does not replace Codex authentication.

## Request flow

1. `inference-gateway start` records the existing Codex route and points Codex at `127.0.0.1`.
2. The gateway parses the request into messages, completed tool-call pairs, and tool definitions.
3. Deterministic rules mark context that must be retained.
4. Jev scores only eligible candidates for relevance to the current request.
5. Candidates below the configured cutoff are removed; retained content is never rewritten.
6. The reduced request is forwarded to the normal Codex upstream and the response is streamed back.
7. On clean shutdown, the previous Codex route is restored.

## Failure behavior

If scoring fails, times out, or returns an incomplete result, the original request is forwarded unchanged. The dashboard binds only to a loopback address. A route changed manually after startup is not overwritten during shutdown.

## Local data

Configuration, the saved Jev key, process state, and optional request logs live in `~/.inference-gateway`. Logs may contain sensitive prompts, source code, tool results, and responses. Logging can be disabled, and `inference-gateway uninstall` removes the complete data directory.

## Trust boundaries

- Codex credentials remain managed by Codex.
- Context selected for scoring is sent to the configured Jev service.
- Requests forwarded through the gateway are sent to the configured Codex upstream.
- The installer and updater verify release binaries against published SHA-256 checksums.

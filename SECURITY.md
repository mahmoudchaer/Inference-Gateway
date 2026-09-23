# Security

## Supported versions

| Version | Supported |
| --- | --- |
| Latest release | Yes |
| Older releases | No |

## Reporting a vulnerability

Please do not open public issues for vulnerabilities, API keys, or accidentally exposed prompt data. Use GitHub's **Security → Report a vulnerability** page so the report can be reviewed privately.

Include the affected version and platform, reproduction steps, impact, and any suggested mitigation. You should receive an initial acknowledgement within seven days. Please allow a reasonable remediation window before public disclosure.

## Security model

Inference Gateway listens only on the local loopback interface. Request logging is enabled by default and may contain source code, prompts, tool results, and model output. Disable it with `inference-gateway config --logs off` when handling sensitive material.

Release binaries are built by GitHub Actions and accompanied by SHA-256 checksums. Never install binaries or scripts from unofficial mirrors.

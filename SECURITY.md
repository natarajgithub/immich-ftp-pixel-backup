# Security Policy

## Supported versions

Security fixes are applied on the latest `main` release (`0.1.x`).

## Reporting a vulnerability

Please open a **private** GitHub security advisory on this repository, or email the maintainer via the address on your GitHub profile if advisories are unavailable. Do not post exploit details in public issues.

## Important deployment warnings

This project is designed for **trusted LAN use**:

- The web portal has **no authentication**.
- API keys, webhook tokens, and FTP passwords are visible and editable in the UI and stored in YAML on disk.
- **Do not** expose port `8088` (or the container port) to the public internet or an untrusted network.
- Prefer binding to a private interface / firewall allow-list for your LAN only.

Treat `/config/users.yaml` like a secrets file (permissions, backups, and access control).

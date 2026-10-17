# Setup

## Prerequisites

- Python 3.12+
- `requests` library (`pip install requests`)

## API Keys

You need one or both API keys (a local-only setup works without a cloud key):

1. **Site Manager API key** — for cloud access (works remotely)
   - Go to [unifi.ui.com](https://unifi.ui.com) → Account → API Keys → Create
   - This proxies requests through Ubiquiti's cloud to your console

2. **Local gateway API key** — for direct LAN access (faster, preferred when on-site)
   - Go to your console's local UI → Settings → API → Create
   - Requires the gateway IP (e.g. `192.168.0.2`)

The skill auto-detects which to use: local gateway when reachable, cloud connector when remote.

## Configuration

Create `config.json` next to SKILL.md (gitignored). Start from `config.json.example`.

```json
{
  "api_key": "YOUR_SITE_MANAGER_API_KEY",
  "gateway_ip": "192.168.0.2",
  "local_api_key": "YOUR_LOCAL_API_KEY"
}
```

| Field | Required | Description |
|---|---|---|
| `api_key` | For cloud commands | Site Manager API key (cloud access). Not needed for local-only use |
| `gateway_ip` | For local access | Local gateway/console IP address |
| `local_api_key` | For local access | Local gateway API key |
| `gateway_fingerprint` | For local access | SHA-256 fingerprint of the gateway's TLS certificate (certificate pinning is mandatory) |
| `site_id` | No | Default site UUID (auto-detected if only one site) |

Alternatively, use environment variables: `UNIFI_API_KEY`, `UNIFI_GATEWAY_IP`, `UNIFI_LOCAL_API_KEY`.

## Local HTTPS & Certificate Pinning

Local gateway requests always use HTTPS and are pinned to that exact certificate — more secure than CA-based verification and works with self-signed certificates.

**Get your gateway's certificate fingerprint:**

```bash
openssl s_client -connect 192.168.0.2:443 </dev/null 2>/dev/null \
  | openssl x509 -noout -fingerprint -sha256
```

This outputs something like:

```
sha256 Fingerprint=FC:4B:F1:4D:FD:DD:F5:A4:B1:30:45:5E:5B:DA:D4:2E:...
```

Copy the fingerprint (everything after `=`) into `config.json`:

```json
{
  "api_key": "YOUR_SITE_MANAGER_API_KEY",
  "gateway_ip": "192.168.0.2",
  "local_api_key": "YOUR_LOCAL_API_KEY",
  "gateway_fingerprint": "FC:4B:F1:4D:..."
}
```

`gateway_fingerprint` is **required** for local access: without it the skill refuses to talk to the gateway ("No gateway_fingerprint configured. Local HTTPS requires certificate pinning.").

> **Note:** If the gateway's certificate changes (e.g. after a firmware update or reset), update the fingerprint in `config.json`.

## Least privilege

API keys inherit the role of the UniFi user that creates them. To make a key read-only, create it while signed in as a **View Only** user: `set-*` and `label-client` commands will then fail. (A key from a View Only user reports `site_role: "readonly"` and `is_super: false` via `GET /proxy/network/api/s/default/self`.)

## Cloud Connector Requirements

- Console firmware **≥ 5.0.3** (older firmware cannot be reached via cloud connector)
- Console must be registered and connected at [unifi.ui.com](https://unifi.ui.com)

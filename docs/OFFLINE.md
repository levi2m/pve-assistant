# Offline by design

The assistant has **no cloud component**. The model runs on your hardware through Ollama, the tool servers run as local processes, state is SQLite and JSON files in one directory. There is no account, no API key for an AI provider, no telemetry, and no phone-home in this repository's code.

The only online piece is the **optional Telegram bridge**, and it can be left out entirely.

## Where everything runs

| Part | Runs | Talks to |
|---|---|---|
| Language model | your Ollama host | nothing outside your network |
| Assistant service (gate, loop, recipes, alerts, memory) | your assistant host | your Ollama, your tool servers |
| Proxmox, Zabbix, Docker, Ollama, OpenWrt tool servers | local child processes of the service | only the system each one controls, at the address you configure |
| State (audit log, memory, transcripts, snapshots) | local directory | nowhere |
| HTTP API | your LAN (bind address is yours) | whoever has the key |
| **Telegram bridge (optional)** | your assistant host | **`api.telegram.org`**, outbound only |

You can check the code claim yourself: the only external address written in `src/` and `mcp_servers/` is the Telegram API URL (`grep -rn "https\?://" src mcp_servers`). Every other address comes from your `config.toml`.

## What goes to Telegram if you enable it

Your chat messages, the assistant's answers, and alert text (host names, problem names, your earlier fix comments, the suggestion line). Telegram is a third-party cloud service. It sees that text and who sent it. Secrets are redacted from transcripts, but alerts are not a place for secrets: treat Telegram as an untrusted channel for anything you would not put in a chat app.

## Running with no Telegram at all

Leave `telegram.env` out. At start the service logs `no Telegram settings: bridge and alerts disabled`.

| Still works | Off |
|---|---|
| Questions and changes through the HTTP API (`POST /ask`) | Proactive alerts and their buttons |
| Slash commands through the API (`/status`, `/memory`, `/remember`, `/approve`, `/digest`, `/review`) | `/alerts`, `/fixes`, `/mute` (they answer "Alerts are off") |
| Undo timers (a disabled host comes back by itself) | Telegram notices about timers and the review |
| The daily review (saved to `<state_dir>/digests`, read with `/digest`) | |
| Gate, deny list, recipes, memory, audit, dry-run | |

For alerts without a cloud service, keep using **Zabbix's own media types** (e-mail to your server, a webhook to a self-hosted notifier). A self-hosted alert channel for the assistant itself (for example ntfy or Matrix) is an open contribution.

## Air-gapping the host

1. Do the setup-time downloads first: `ollama pull <model>`, `git clone` + `npm ci` + `pip install` (see `scripts/build_mcp_servers.sh`), and any Docker images you want. These need internet once.
2. Omit `telegram.env`.
3. Block outbound traffic from the assistant host at your firewall. Nothing in the service needs it. (The bundled Docker server has a `pull image` tool; it works only if you allow the Docker host to reach a registry.)
4. Prove it: `ss -tnp` on the assistant host shows only connections to your own LAN addresses. With Telegram enabled it shows one extra connection to Telegram's address range, and nothing else.

## Third-party servers

`mcp-pve` and `zabbix-mcp-server` are separate programs. At the pinned versions they were reviewed for network behaviour: the Proxmox server talks only to the Proxmox URL you give it; the Zabbix server has an update check and an AI-template feature, both **off** in the example config (`update_check_enabled = false`, no AI key). Re-check this when you move a pin, and run the block in step 4 to confirm.

## Status of this mode

The Telegram-less path was run on a real lab for the HTTP API, `/status`, `/memory` and the daily review. The local undo-timer loop that runs without Telegram is covered by a unit test but has **not** been run end to end on a live lab yet.

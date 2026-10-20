# Operations

## Commands (Telegram, from the configured chat only)

`/status` queue, uptime, tool servers, open undo timers · `/clear` forget the last turns · `/remember <fact>` · `/memory` · `/approve N` `/reject N` `/forget N` · `/digest` last daily review · `/review` run it now · `/alerts` what is open and what was sent · `/fixes <text>` search past fixes in Zabbix · `/mute <hours>` Average-level alerts only · `/unmute` · `/help`.

"Add to your memory that …" is handled by code on every channel (it is really saved; instruction-like text is refused).

## HTTP API

`POST /ask {"question": "...", "session": "optional"}`, `GET /status` (both need header `X-Api-Key`), `GET /health` (no key; 503 when a tool server keeps failing – meant for Zabbix or another monitor). Rate limit 10 requests/min, 1000 characters per question. Bind it to the interface you need (`ASSISTANT_API_BIND`); without `ASSISTANT_API_KEY` the API is disabled.

## Files

| Path | Content |
|---|---|
| `/etc/pve-assistant/` | `config.toml` and the env files (secrets) |
| `/var/lib/pve-assistant/audit.db` | every request and tool call, memory, alerts, undo timers |
| `…/log/YYYY-MM-DD.jsonl` | transcripts (redacted, 90 days, mode 0600) |
| `…/snapshots`, `…/digests` | nightly lab snapshots and review texts |
| `…/eval-*.json`, `eval-candidates.jsonl` | evaluation results; failures worth turning into cases |

## Deploying a change

1. Edit, run `python tests/run_all.py`, copy `src/pve_assistant` to `/opt/pve-assistant/app/`.
2. `python -m pve_assistant gate-eval` on the assistant host.
3. Restart **only when idle**: poll `/status` until `Queue: 0`, then `systemctl restart pve-assistant`.
4. The service exits on its own after 3 consecutive transport failures of a tool server and systemd restarts it after 15 s.

## Routine

- Read the **nightly digest**: "answers to review" are the places where the model was probably wrong; "lab changes" is a free inventory diff; "eval candidates" are tomorrow's test cases.
- After a Proxmox/Zabbix/MCP upgrade: `gate-eval` (deny list still complete?), then `answer-eval`.
- After a new guest or container: add its name to `[hints]` so questions about it are routed.
- Open undo timers (`/status`) are things the assistant disabled and will re-enable; they survive restarts.

## Backups

The assistant keeps its state in one directory and can be rebuilt from this repository and your env files. Back that directory up with your normal tooling if you want to keep memory and history. The assistant is **not** allowed to touch backups or backup jobs – by design, see SAFETY.md – so schedule and verify backups yourself.

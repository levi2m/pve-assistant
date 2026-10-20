# pve-assistant

A small-model assistant for a Proxmox lab that is monitored with Zabbix. You talk to it in Telegram (or over a small HTTP API); it answers questions about the lab, makes **one reversible change per request**, and tells you about problems before you look at a dashboard.

It runs a **4-billion-parameter model on CPU** (default `qwen3.5:4b` through Ollama). That is the point of the project and the reason for most of its design: a model this small cannot be trusted with hundreds of tools, free-form arguments or its own safety rules, so **code decides what the model may see and do**, and the model only chooses between a handful of well-described tools.

> Status: working in a real lab and evaluated there (see [docs/EVALUATION.md](docs/EVALUATION.md)); the generic, configurable version in this repository has been re-tested offline and against that lab, but not yet on a second, different lab. Treat the first week on your own lab as a trial: leave the assistant in dry-run mode first (`ASSISTANT_DRYRUN=1`).

## What it does

- **Answers questions** from live data: guests and their state, storage, Zabbix problems, Docker containers, Ollama models, router status, n8n workflows. Counts and severities are computed in code and handed to the model, never counted by it.
- **Makes one change per request**, with read-back verification: start/stop/reboot Proxmox guests (it waits for the Proxmox task and reports how it really ended), Docker start/stop/restart, and ten flat Zabbix operations (acknowledge, comment, change severity, close, enable/disable a host/item/trigger with an automatic undo timer, set a macro, change host-group membership).
- **Alerts you proactively** from Zabbix: High and Disaster at once, Average after it stays open, quiet hours, flapping and burst control, reminders, "resolved" notices, Acknowledge and Details buttons, and a one-line suggestion that comes from *your own earlier fix comments* in Zabbix plus live evidence.
- **Reviews its own day** (deterministic, not model-written): usage, failures, slow answers, what changed in the lab since yesterday.
- **Remembers facts, never instructions**: notes the owner approves are added to the prompt; anything that reads like an instruction is refused.

## What it will not do

Delete guests or storage, touch backups, change users/tokens/firewall/cluster/HA, or run any raw Zabbix write. These are refused **in code**, whatever the model says. See [docs/SAFETY.md](docs/SAFETY.md).

## How it is built

```
  Telegram ◀──▶ bridge ┐                    ┌─ pve     (Proxmox MCP)
  HTTP API ───────────┘                    ├─ zabbix  (Zabbix MCP)
         queue, one request at a time      ├─ docker  (thin MCP in this repo)
              │                            ├─ ollama  (thin MCP in this repo)
            GATE  picks 6-8 of ~470 tools  ├─ openwrt (thin MCP in this repo, read-only)
              │                            └─ n8n     (optional)
            LOOP  model ⇄ tools, ≤4 steps, one change per request
              │
          RECIPES (Zabbix operations)   ALERTS (Zabbix poller)   NIGHTLY review   MEMORY
```

Everything is plain Python on top of the MCP SDK. The tool servers are separate processes, so the assistant never talks to Proxmox or Zabbix by any route other than their MCP servers. Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick start

1. **Prerequisites**: Proxmox VE, Zabbix 7.0+ (the project is developed against 8.0), [Ollama](https://ollama.com) with a tool-calling model, a Telegram bot, a machine or LXC container (Python ≥ 3.11, Node ≥ 20) for the assistant. About 1 GB of RAM for the assistant itself and 4–8 GB for the model.
2. Follow [docs/SETUP.md](docs/SETUP.md): create the Proxmox token, the Zabbix user and a narrow Zabbix role, build the two third-party MCP servers (`scripts/build_mcp_servers.sh`), write `config.toml` from [deploy/config.example.toml](deploy/config.example.toml), install the systemd unit.
3. Check it without a model: `python -m pve_assistant gate-eval` (does the gate offer the right tools?) and `python tests/run_all.py` (offline tests).
4. Ask it something: `python -m pve_assistant ask "which guests are running?"`.

```
git clone https://github.com/levi2m/pve-assistant.git
cd pve-assistant && git checkout dev
python tests/run_all.py        # no lab needed
```

## Repository map

| Path | What |
|---|---|
| `src/pve_assistant/` | the assistant: `gate.py`, `loop.py`, `recipes.py`, `alerts.py`, `service.py` (Telegram + API), `nightly.py`, `memory.py`, `timers.py`, `registry.py`, `config.py` |
| `src/pve_assistant/gate_eval.py`, `answer_eval.py` | the two evaluation harnesses |
| `mcp_servers/` | thin MCP servers for Docker, Ollama/Open WebUI and OpenWrt (read-only) |
| `scripts/` | Zabbix role/health item/safety net, disposable Zabbix fixture, third-party build, router login, repository scanner |
| `deploy/` | example config, env templates (no values), systemd unit |
| `tests/` | offline tests: gate, alert engine with a fake clock, compaction, redaction, memory, timers, config |
| `docs/` | architecture, setup, safety model, lessons learned, evaluation method, operations, secrets rotation |
| `AGENTS.md` | rules and commands for AI coding agents working on the repository |

## Third-party software

The Proxmox and Zabbix MCP servers are **fetched at pinned versions, not vendored**: [mcp-pve](https://github.com/Samik081/mcp-pve) (MIT) and [zabbix-mcp-server](https://github.com/initMAX/zabbix-mcp-server) (AGPL-3.0, run as a separate process). Review them before moving the pins. This repository is MIT-licensed (see `LICENSE`).

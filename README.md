<div align="center">

# pve-assistant

### A homelab assistant that runs on a **4B model, on CPU, with no GPU**, and that you can actually trust near your Proxmox.

Ask it about your lab in Telegram. It makes **one reversible change per request**, tells you about problems before you open a dashboard, and **physically cannot delete a guest, touch a backup, or run a raw Zabbix write**, because those rules live in code, not in a prompt.

[![CI](https://github.com/levi2m/pve-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/levi2m/pve-assistant/actions/workflows/ci.yml)
![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Model](https://img.shields.io/badge/model-4B%20on%20CPU-green)
![MCP](https://img.shields.io/badge/MCP-Proxmox%20%C2%B7%20Zabbix%20%C2%B7%20Docker%20%C2%B7%20Ollama%20%C2%B7%20OpenWrt-orange)

</div>

---

## The idea in one paragraph

Everyone's first homelab-AI demo gives a big model a pile of tools and hopes. That doesn't survive contact with a small local model: hundreds of tool schemas don't fit in its context, it fills optional arguments wrongly, it cannot count, and in testing **every small model I tried obeyed "stop every container"**. So this project flips the design: **the model is treated as an untrusted, helpful component**. Code decides which 6–8 tools it sees, hides the arguments it gets wrong, computes every count and severity itself, verifies every change by reading it back, and refuses the dangerous operations no matter what the model says.

The result is an assistant that is boring to trust, fast enough to use (13–60 s per answer on CPU), and small enough to read in an afternoon.

## What it looks like

*(Illustrative conversation; names changed.)*

```
you  ▸ which guests are running?
bot  ▸ 5 of 6 guests are running: web01, db01, proxy, git, media.
       Stopped: template-debian.

you  ▸ anything critical on zabbix?
bot  ▸ 7 active problems: 1 average, 6 warning. Most severe: average.
       - Linux: Load average is too high (web01, average)
       - ... 

you  ▸ restart the proxy container in docker
bot  ▸ Restarted "proxy" (was running, up 3 days → running, up 4 s). Verified through the Docker API.

you  ▸ disable monitoring of host web01 for 3 hours
bot  ▸ Host web01: enabled → disabled. It will be enabled again automatically at 18:42.

you  ▸ delete container 105
bot  ▸ I can't do that: deleting guests is blocked on purpose.
```

And without you asking, in Telegram, with **Acknowledge** and **Details** buttons:

```
🔴 HIGH · web01 | Linux: Load average is too high
Open 3 min (since 14:02) · event 1234
Seen 3 times in the last 7 days.
Earlier fix noted in Zabbix: "stress test left running, killed it"
Suggestion: check whether the stress test is still running; it caused this last time.
```

The suggestion line comes from **your own earlier acknowledgement comments in Zabbix** plus live evidence, and code throws it away if it contains an invented number, command or URL.

## The numbers

Measured on a real lab, 4B model (`qwen3.5:4b` via Ollama), CPU only:

| | |
|---|---|
| Tools available to the assistant | **400+** (Proxmox 122, Zabbix 249, Docker/Ollama/OpenWrt, optional n8n) |
| Tool schema if you gave them all to the model | **~265,000 tokens** (the model's usable context is 4–8k) |
| Tool schema per request after the gate | **~650 tokens on average**, 1,232 at most |
| Tools the model sees per request | **6–8** |
| Proxmox tools denied in code | **44**, proven never offered and always refused on every `gate-eval` run |
| Raw Zabbix write tools hidden and refused | **163** |
| Zabbix write methods the API role allows | **8** (Zabbix itself refuses everything else) |
| Invalid tool calls from the 4B model | **0** in two 35-call runs |
| Answer evaluation (ground truth read live, never through the model) | **31/31** and **30/31** |
| Gate evaluation | **64/64**, no model needed |
| Alert-engine tests (fake clock, fake Zabbix, fake Telegram) | **44/44** |
| Answer latency | 13–60 s on CPU (about 5–6 tokens/s). Slow, honest |

The run-to-run difference of one case is noise; see [docs/EVALUATION.md](docs/EVALUATION.md) for how it was measured and what the other models did.

## Why it's different

**1. The gate.** BM25 + synonyms + server routing, no model call, picks 6–8 tools per question. Write tools are hidden unless the question contains an action verb. IDs and IP addresses route to the right system. This single component is why a 4B model works at all.

**2. Code does the arithmetic.** Lists reach the model as whole rows with a `TOTAL n` line and a `BY SEVERITY` line computed in code. The model used to say "8 problems" for 9 and add `11 + 6 = 17`. Never cut mid-data; say `SHOWING x OF y`.

**3. Hidden arguments.** Free optional arguments were the main source of wrong answers (`limit=1` → "there is 1 problem"; `query="n8n"` → "0 workflows"; "critical" → the *lowest* severity). The model never sees them; code sets safe values.

**4. One change per request, verified.** The first write that really changes something runs; the rest are refused. Every change is read back and reported as `before → after`. Proxmox only *starts* a task, so the loop waits for it and reports how it really ended.

**5. Undo timers.** Disable a host, item or trigger and it comes back by itself (default 4 h, persisted across restarts).

**6. Alerts you will keep.** In one real week a lab raised 237 problem events and a single flapping trigger made 40 of 48 "average" ones. Persistence, flapping collapse, burst grouping, an hourly cap and quiet hours turned that into a channel worth reading.

**7. Memory holds facts, never instructions.** Notes you approve go into the prompt; anything that reads like an instruction is refused, even from you. The assistant cannot change its own rules, deny list or code.

**8. A safety net outside itself.** If the assistant is down, or a High problem sits unacknowledged, **Zabbix** messages you directly.

## Safety at a glance

```
systems' own permissions  →  deny list in code  →  raw writes hidden  →  one change per request
→  delete-mode filtering  →  recipe guards  →  untrusted-data wrapping  →  memory refuses instructions
→  dry-run mode  →  audit + redacted transcripts  →  Zabbix safety net
```

The deny list covers deleting guests, storage and snapshots; snapshot rollback; creating, running or deleting backups and backup jobs; HA; users, tokens and ACLs; firewall, network and cluster options; storage config; bulk actions; node services. Read [docs/SAFETY.md](docs/SAFETY.md), including the **residual risks** (Docker's API is root on that host; n8n's MCP can build its own workflows and is off by default; a new upstream tool can bypass a name-based deny list, so re-run `gate-eval` after upgrades).

## How it works

```
  Telegram ◀──▶ bridge ┐                    ┌─ pve     (Proxmox MCP)
  HTTP API ───────────┘                    ├─ zabbix  (Zabbix MCP)
         queue, one request at a time      ├─ docker  (thin MCP in this repo)
              │                            ├─ ollama  (thin MCP in this repo)
            GATE  picks 6-8 of 400+ tools  ├─ openwrt (thin MCP in this repo, read-only)
              │                            └─ n8n     (optional, off by default)
            LOOP  model ⇄ tools, ≤4 steps, one change per request
              │
          RECIPES (Zabbix operations)   ALERTS (Zabbix poller)   NIGHTLY review   MEMORY
```

Plain Python on the MCP SDK; tool servers are separate stdio processes, so the assistant reaches Proxmox and Zabbix by no route other than their MCP servers. More in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## What it can do

- **Ask:** guests and their state, storage, Zabbix problems and hosts, Docker containers, Ollama models, router WAN/Wi-Fi/devices, n8n workflows.
- **Change (one per request):** start / stop / reboot Proxmox guests; Docker start / stop / restart; ten flat Zabbix operations: acknowledge, comment, change severity, close, enable/disable host/item/trigger with undo timer, set a macro (secret-looking names refused), change host-group membership.
- **Alert:** proactive Telegram alerts with Acknowledge / Details buttons, reminders, "resolved" notices, `/mute`, `/alerts`, `/fixes <text>` to search your past fixes.
- **Review:** a deterministic nightly digest: usage, failures, slow answers, and a diff of what changed in your lab since yesterday.
- **Remember:** `/remember`, `/memory`, `/approve`: facts only.

## What it deliberately won't do

Delete guests or storage · touch backups or backup jobs · change users, tokens, firewall, cluster or HA · run any raw Zabbix write · rewrite its own rules · take instructions from text it reads in your lab.

## Quick start

You need: Proxmox VE, Zabbix 7.0+ (developed against 8.0), [Ollama](https://ollama.com) with a tool-calling model, a Telegram bot, and somewhere to run it (an LXC with Python 3.11+, Node 20+, ~1 GB RAM; 4–8 GB for the model).

```bash
git clone https://github.com/levi2m/pve-assistant.git && cd pve-assistant
python tests/run_all.py                     # 17 offline test functions, no lab needed
cp deploy/config.example.toml /etc/pve-assistant/config.toml   # then follow docs/SETUP.md
sh scripts/build_mcp_servers.sh             # fetches the two third-party MCP servers at pinned commits
python -m pve_assistant gate-eval           # does the gate offer the right tools? is the deny list intact?
python -m pve_assistant ask "which guests are running?"
```

Start in **dry-run** (`ASSISTANT_DRYRUN=1`): writes are logged, never executed, and the answer says nothing was done. [docs/SETUP.md](docs/SETUP.md) walks through least-privilege tokens, the narrow Zabbix role, Telegram, systemd and going live.

## What I learned (the part worth reading even if you never run it)

The full write-up is [docs/LESSONS.md](docs/LESSONS.md). A few that surprised me:

- A 4B model **cannot be a security boundary**: every small model tested attempted `stop` on the containers at least once when asked to "delete or stop everything" (the 4B in one early run, the others every time); one disarmed Proxmox HA during an evaluation. Injection hidden in *tool output* was never followed. The explicit user request is the weak point.
- **Most "model failures" were my test harness**: a stale ground truth, a regex that could never match, statistics that labelled failures "ok". Read every failing answer first.
- **Tools can silently do nothing.** The Zabbix MCP's acknowledge call accepts a "snooze" that Zabbix ignores without `suppress_until`. Only read-back caught it.
- **Never turn an error into an empty result.** A renamed Zabbix option made "known fixes" fail silently for every alert for hours.
- **Positive controls for scanners.** The secret scanner that "found nothing" had a missing comma and was skipping the file that held the real token.
- **Bigger wasn't better here:** the 4B scored best *and* was the safest; the smaller 2B, 3B and MoE models were faster but obeyed the destructive request every time.

## Honest status

- Developed and evaluated on **one real lab**. The generic, configurable version in this repository was re-tested against that lab and offline, but not yet on a second, different lab. Expect to tune `[hints]` for your guest names.
- Proxmox writes (start/stop/reboot) go through the same verified path as the rest, but the evaluation does not yet include a disposable-guest end-to-end test.
- Known gaps are tracked in [AGENTS.md](AGENTS.md#known-gaps-good-places-to-contribute): delete-mode offers delete tools of every matching server, Zabbix "snooze" needs an upstream parameter, the alert Details button has no automated test.
- It is slow by design (small model, CPU). If you want sub-second answers, this is the wrong tool.

## Repository map

| Path | What |
|---|---|
| `src/pve_assistant/` | `gate.py`, `loop.py`, `recipes.py`, `alerts.py`, `service.py` (Telegram + API), `nightly.py`, `memory.py`, `timers.py`, `registry.py`, `config.py` |
| `src/pve_assistant/gate_eval.py`, `answer_eval.py` | the two evaluation harnesses |
| `mcp_servers/` | thin MCP servers for Docker, Ollama/Open WebUI and OpenWrt (read-only) |
| `scripts/` | Zabbix role / health item / safety net, disposable Zabbix fixture, third-party build, router login, repository scanner |
| `deploy/` | example config, env templates (no values), systemd unit |
| `tests/` | offline tests: gate, alert engine, compaction, redaction, memory, timers, config |
| `docs/` | architecture, setup, safety, lessons, evaluation, operations, secrets rotation |
| `AGENTS.md` | rules and commands for AI coding agents working on the repository |

## Contributing

Issues and PRs welcome, especially: a second lab's `[hints]` and results, more evaluation cases, a filtering proxy for Docker's API, and fixes for the known gaps. Read [AGENTS.md](AGENTS.md) first: nothing lab-specific or secret goes in the repo (`scripts/scan_repo.py` checks), and safety invariants don't get weakened to make a test pass.

## Third-party software and credits

The Proxmox and Zabbix MCP servers are **fetched at pinned commits, not vendored**: [mcp-pve](https://github.com/Samik081/mcp-pve) (MIT) and [zabbix-mcp-server](https://github.com/initMAX/zabbix-mcp-server) (AGPL-3.0, run as a separate process). Built on the [Model Context Protocol](https://modelcontextprotocol.io), [Ollama](https://ollama.com) and the Qwen model family. Review the upstream servers before moving a pin: they hold your Proxmox and Zabbix tokens.

## License

MIT, see [LICENSE](LICENSE).

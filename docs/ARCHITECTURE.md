# Architecture

One small local model answers questions and changes the lab only through tool servers. Code around the model decides what it may see and do.

```
  YOU                                             ZABBIX SAFETY NET
  Telegram ◀──▶ Telegram cloud ◀── long-polled     Zabbix messages you itself when the
        buttons    (outbound only; nothing         assistant is down, or a High problem
                    connects in)                   stays unacknowledged
                          │                                 
┌─────────────────────────┴───────────────────────────────────────────────────┐
│ assistant host · service pve-assistant (systemd, unprivileged user)         │
│                                                                             │
│   Telegram bridge                         HTTP API (key, rate limited)      │
│            └──────────────┬─────────────────────┘                          │
│        one request at a time · short queue · memory of the last 3 turns     │
│                           ▼                                                 │
│   GATE   picks 6-8 of ~470 tools · hides write tools unless a change was    │
│          asked for · deny list · raw Zabbix writes hidden · schemas slimmed │
│                           ▼                                                 │
│   LOOP   model ⇄ tools, max 4 steps · one successful change per request     │
│          results cut to whole rows with totals · waits for Proxmox tasks    │
│          └── chat ─────────▶ Ollama (a 4B tool-calling model)               │
│                                                                             │
│   RECIPES  10 Zabbix operations, each verified by read-back                 │
│   ALERTS   Zabbix poll · NIGHTLY review · MEMORY · TIMERS (undo) · SQLite   │
│                           ▼ MCP (stdio child processes, one over HTTP)      │
│   pve        zabbix        docker       ollama       openwrt       n8n      │
└──────┬─────────┬─────────────┬─────────────┬────────────┬───────────┬───────┘
       ▼         ▼             ▼             ▼            ▼           ▼
   Proxmox     Zabbix        Docker        Ollama /     OpenWrt     n8n
   API         API           API           Open WebUI   router      (optional)
   (token)     (narrow role) (see SAFETY)               (read-only login)
```

## Components

| Module | Responsibility |
|---|---|
| `config.py` | one TOML file for everything lab-specific; secrets only in env files next to it |
| `registry.py` | starts the MCP servers (stdio children, or HTTP), indexes every tool with its schema and read/write annotation, registers the Zabbix recipes as virtual tools |
| `gate.py` | per request: BM25 + synonyms + server routing choose 6–8 tools; hides write tools unless the question has an action verb; deny list and delete-mode rules; slims schemas; `check_call` re-validates every call |
| `loop.py` | system prompt, Ollama chat, tool execution, hidden-argument injection (`limit`), result compaction, Proxmox task verification, one-change rule, dry-run mode |
| `recipes.py` | ten flat, name-based Zabbix operations built on the *existing* Zabbix MCP tools (no second route to Zabbix) |
| `timers.py` | persistent undo timers: a disabled host/item/trigger comes back by itself |
| `alerts.py` | polls Zabbix, decides what to send (pure function), flapping/burst/hourly caps, reminders, resolved notices, enrichment, validated one-line suggestion |
| `nightly.py` | the daily review: transcripts → usage, failures, slow answers; lab snapshot diff; alert summary |
| `memory.py` | owner-approved facts; proposals from the nightly review wait for approval; instruction-like text refused |
| `transcript.py` | one JSON line per request, secrets redacted, kept 90 days |
| `audit.py` | SQLite: every request and every tool call with outcome |
| `service.py`, `telegram.py` | Telegram long-poll bridge with buttons; HTTP API; watchdog (3 consecutive transport failures → exit, systemd restarts) |

## A question, step by step

1. The message arrives (Telegram or API) and waits its turn; slash commands are handled by code.
2. The gate chooses 6–8 tools from ~470 and hides write tools unless a change was asked for.
3. The model picks a tool; code checks the arguments, the deny list and the one-change rule, and adds hidden arguments itself.
4. The tool server calls the real system; the result is cut to **whole rows with the total stated**.
5. A Proxmox change is awaited (Proxmox only *starts* a task) and reported as it really ended.
6. The answer returns; the exchange is saved with secrets removed.

## An alert, step by step

1. Every 60 s the assistant reads active problems from Zabbix; a persistent table remembers what was announced, so a restart never repeats or loses an alert (the first run is a silent baseline for everything below High).
2. A pure function decides: High+ now, Average after `persist_min`, nothing in quiet hours below High, never Warning/Information.
3. Flapping becomes one notice; bursts and hourly overflow become one grouped message.
4. Enrichment within a time budget (it never blocks the alert): how often it happened, your earlier fix comments from Zabbix, live evidence from read tools, one suggestion line that code validates and drops if it invents numbers, commands or URLs.
5. Buttons: Acknowledge (the same recipe as chat) and Details (the assistant investigates).
6. If the assistant is down, Zabbix itself messages you (the safety net, `scripts/zabbix_setup.py safetynet`).

## Design choices worth knowing

- **stdio MCP children, no listeners.** The assistant owns its tool servers; there is no network surface between them and no bearer token to leak.
- **No second route to a system.** Recipes use the same Zabbix MCP tools the model could see; the n8n server is optional because it is a second route (see SAFETY).
- **State is small and local.** SQLite plus JSON lines in one directory. Losing it loses history and approved memory, not function.

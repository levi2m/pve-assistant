# AGENTS.md

Instructions for AI coding agents (and humans in a hurry) working in this repository. Read it before changing anything. Deeper background: `docs/` (especially `SAFETY.md` and `LESSONS.md`).

## What this project is

A small-model (4B, CPU) assistant for a Proxmox lab monitored by Zabbix. It talks to the lab only through MCP tool servers. **Code decides what the model may see and do**; the model only chooses among a few well-described tools. Package: `src/pve_assistant/`. Thin MCP servers: `mcp_servers/`. Setup scripts: `scripts/`. Deploy templates: `deploy/`. Tests: `tests/`.

## Commands

```
python tests/run_all.py                    # offline tests, no lab, no model, seconds. Run after EVERY change.
python scripts/scan_repo.py                # secret / IP / e-mail / chat-id scanner (has a selftest). Run before every commit.
python -m pve_assistant gate-eval          # needs tool servers, no model: right tools offered? deny list intact?
python -m pve_assistant answer-eval <model># needs the model; see docs/EVALUATION.md
python -m compileall -q src mcp_servers scripts tests
```

`gate-eval`, `answer-eval` and `serve` need a real lab and a config (`ASSISTANT_CONFIG=/path/config.toml`). If you do not have one, say so; do not claim they passed.

## Hard rules

1. **Nothing lab-specific in the repository.** No real hostnames, IP addresses, guest or container ids, node names, chat ids, e-mail addresses, timezones, tokens, passwords, keys, or screenshots/logs from a real lab. Use placeholders: `pve1`, `web01`, `192.0.2.x` (documentation range), `example.net`. Everything that differs per lab goes in the TOML config (`deploy/config.example.toml`); secrets go in env files that are git-ignored (`*.env`, `config.toml`, `zabbix-mcp.toml`). A fixture that needs a secret-shaped string builds it at run time (see `tests/test_building_blocks.py`).
2. **Never weaken a safety invariant** to make a test or a demo pass. They are listed in `docs/SAFETY.md`; the ones that matter most: the deny list (`DENY_NAMES` in `gate.py`), raw Zabbix write tools hidden and refused, one successful change per request, delete-mode filtering, recipe guards (protected hosts, secret-looking macros), memory refuses instruction-like text, dry-run never executes. Changing, adding to or removing any of them is a decision for the owner: propose it, do not slip it into an unrelated change.
3. **No new write power without the owner.** A new tool that changes a system, a wider Zabbix role method list, a bigger Proxmox privilege, a new server enabled by default: ask first.
4. **The model is not a security boundary.** Put any rule that matters in code (gate, `check_call`, recipes, the systems' own permissions), never only in the prompt.
5. **Never print, log, commit or paste secrets.** Not in output, not in test fixtures, not in docs. If one shows up anywhere, say so and tell the owner to rotate it (`docs/SECRETS.md`).
6. **Do not run destructive prompts or writes against a live lab.** Use `ASSISTANT_DRYRUN=1`. Real writes only on a disposable object whose state you verify through a different API, with cleanup verified.
7. **Do not start a second Telegram poller with a live bot token** (it steals updates from the running service). Test the service with Telegram disabled and the API on a test port.
8. **Do not push, tag, force-push or rewrite published history** unless the owner asks.
9. **Stay offline-first.** The only external address in the code is the optional Telegram API. Do not add telemetry, update checks, cloud model calls or any other outbound endpoint; a new network egress must be opt-in, off by default, and documented in `docs/OFFLINE.md`. The service must keep working with Telegram removed.
10. **Git commits:** use the identity configured in the repository; add **no** AI attribution of any kind (no trailers, session links or tool footers) to commit messages, pull requests or files. Short imperative subject, body explaining why.

## Code conventions

- Python >= 3.11, standard library first; the only dependency is `mcp` (pinned in `pyproject.toml`). Keep modules small; put decisions in pure functions that tests can call with a fake clock (see `alerts.py`).
- Match the surrounding style: comment density, naming, idiom. Docstrings state intent and the reason for a non-obvious rule; do not narrate the obvious.
- Lab-specific names reach the code only through `config.py` (`get(...)`). Routing words for a lab's guests go in `[hints]` with `\b` word boundaries.
- Errors from a tool server or API must reach the user. Never turn an error into an empty result.
- Anything the model receives must be bounded: whole rows with an exact `TOTAL`, never cut mid-data, no numeric side fields that a small model will add together.
- Free optional arguments are the main source of wrong answers from a 4B model: hide or sanitise them in code (`SLIM_DROP`, hidden `limit`).

## Testing rules

- **Ground truth comes from direct tool calls, never from the model**, read just before and after a question for anything that drifts.
- **Read every failing answer before blaming the model.** Most past "failures" were harness bugs.
- **Positive controls:** a scanner or check that finds nothing must be shown to find a planted sample.
- Keep tuned and held-out question sets separate; after fixing a held-out miss, write new held-out cases.
- A difference of one case between two identical runs is noise; repeat before concluding.
- When you change `gate.py`, `recipes.py` or `alerts.py`, add or adjust an offline test in the same change.
- Report results honestly: what ran, what passed, what you could not run.

## Third-party servers

`mcp-pve` (MIT) and `zabbix-mcp-server` (AGPL-3.0) are fetched by `scripts/build_mcp_servers.sh` at pinned tags **and commit SHAs**, and run as separate processes. Do not vendor them and do not move a pin without reviewing the upstream diff; they hold the Proxmox and Zabbix tokens. After moving a pin run `gate-eval` (the deny list is by tool name, so new upstream tools can bypass it).

## Operating a deployment

- Restart the service only when idle: poll `/status` until `Queue: 0`.
- Deploy = copy `src/pve_assistant` to the host, `gate-eval`, restart. Config and env files live outside the repository.
- Deployment-specific notes (which host, which addresses, which owner decisions) belong in the operator's own inventory, not here.

## Known gaps (good places to contribute)

- Delete mode offers delete tools of every matching server (e.g. n8n `delete_agent` for "delete container N"); restrict it to the server the question names.
- Zabbix "snooze" needs `suppress_until`, which the Zabbix MCP's `event_acknowledge` does not expose.
- The Proxmox write path (start/stop/reboot) has no disposable-guest end-to-end test in the evaluation.
- The alert "Details" button has no automated test.
- Docker's remote API is used unauthenticated; a filtering proxy would be safer.

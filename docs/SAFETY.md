# Safety model

The model is **not** a security boundary. A 4B model will follow an explicit request to "delete everything", and any model can be talked into things by text it reads. So every rule that matters is enforced in code or by the systems being called, and the model is treated as an untrusted component that is helpful most of the time.

## Layers, outermost first

1. **The systems' own permissions.** The Proxmox token has only the roles you gave it. The Zabbix role allows 8 write methods; Zabbix itself answers "No permissions to call …" for everything else (deletes, creates, templates, actions, users). The router login is read-only by ACL.
2. **The deny list** (`gate.py`, `DENY_NAMES`): about 44 Proxmox tools that are never offered and always refused by `check_call` – deleting guests, storage and snapshots; snapshot rollback; running, creating or deleting backups and backup jobs; HA changes; users, tokens, ACLs; firewall, network and cluster options; storage configuration and pools; bulk actions; node services; stopping tasks. `gate-eval` proves three things on every run: each name still exists in the server, `check_call` refuses it, and no probe question ever offers it.
3. **Raw Zabbix write tools are hidden** (about 160 of them) and refused. The ten recipes call a few of them internally; the model never sees the raw tools. Zabbix write tools take nested `params` objects that a 4B model cannot fill reliably, which is a safety problem as much as a quality one.
4. **One successful change per request.** The first write that really changes something runs; any further write in the same request is refused. A "stop every container" request becomes one stop and five refusals (tested in dry-run).
5. **Delete mode.** When the question is about deleting, only tools that delete are offered. Without this, the model substitutes look-alike actions (disable the host, stop the containers) for the delete it cannot do.
6. **Recipe guards.** Protected hosts (`[zabbix] protected_hosts`) are never disabled; macros whose name suggests a secret (PASS, SECRET, TOKEN, KEY, PSK, CRED, AUTH) and existing secret macros are refused; a host keeps at least one group; durations are 1–72 h; everything disabled comes back by itself (default 4 h) through a persistent undo timer; every change is read back and reported as "before → after" or "already".
7. **Untrusted data.** Tool output is wrapped as data. Text inside guest names, log lines, problem names and alert comments can carry instructions; it is shown to the model only as data, and the gate/deny list does not depend on the model ignoring it.
8. **Memory holds facts, not instructions.** Notes the owner approves become part of the prompt. Anything that reads like an instruction ("ignore", "you may delete", "without asking", "deny list" …) is refused for every source, the owner included.
9. **Self-improvement stops at proposals.** The assistant cannot change its code, deny list, gate rules, prompt or write limit. The nightly review only *proposes* notes and evaluation candidates.
10. **Dry-run.** `ASSISTANT_DRYRUN=1` logs write calls as attempted and never executes them; the answer says that nothing was done.
11. **Audit and transcripts.** Every request and tool call is recorded; transcripts redact JWTs, bot tokens, UUIDs, long hex keys and `key=value` secrets, and are kept 90 days at mode 0600.
12. **The assistant is not the only watcher.** The Zabbix safety net messages you when the assistant is down or a High problem stays unacknowledged.

## Residual risks you accept by using it

- **A new version of a tool server can add tools that bypass the deny list**, because the list is by name. `gate-eval` fails if a listed name disappears, but it cannot know about new ones: re-read the tool list when you move a pin.
- **Docker's remote API is root on that host.** The bundled Docker server speaks to it unauthenticated; do not expose the port beyond the assistant, or put a proxy that allows only the calls you need in front of it.
- **Delete mode is cross-server.** For a question about deleting something, the gate offers tools that delete from *every* server that matches, not only the one you meant (observed: asking to delete a container offered an n8n `delete_agent` tool). The model has no id to give it, the one-change rule and each system's own permissions still apply, and in testing it produced a tool error; but it is one more reason to keep optional servers that have delete tools (n8n) disabled unless you need them.
- **n8n.** Its built-in MCP lets a caller create and run workflows with HTTP, Code and SSH nodes: that is a way around every list above. It is therefore **off by default**; enable it only for a read-only token or a protected instance, and set `[n8n] protected_workflow_prefixes` for workflows that must never be run or edited.
- **The model still gives wrong prose sometimes** (calling a warning "critical", suggesting shell commands it was not given). Numbers and severities come from code; read the "answers to review" list in the nightly digest.
- **Telegram is a cloud service.** Bot messages leave your network. Alerts contain host and problem names; they should not contain secrets, and the redaction is a net, not a promise.
- **Secrets.** Tokens in env files are readable by the service user; compromise of the assistant host means compromise of those tokens. Give the host to nothing else, and rotate (SECRETS.md).

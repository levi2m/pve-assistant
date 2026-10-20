# Setup

Order matters: tokens and roles first (least privilege from the start), then the assistant, then dry-run, then live.

## 0. Where it runs

A small LXC container or VM next to Ollama is enough: Debian 12/13, Python ≥ 3.11, Node ≥ 20, ~1 GB RAM for the assistant (the unit caps it at 1200 MB). Ollama needs 4–8 GB depending on the model and context. Give the assistant host its own unprivileged user (`assistant` below).

```
useradd --system --home /var/lib/pve-assistant --shell /usr/sbin/nologin assistant
install -d -o assistant -g assistant -m 750 /var/lib/pve-assistant
install -d -o root -g assistant -m 750 /etc/pve-assistant
```

## 1. Proxmox token

The assistant only needs what you want it to be able to do. Create a dedicated user and token; do not reuse root's.

```
pveum user add assistant@pve
pveum user token add assistant@pve assistant --privsep 1      # the secret is shown once: put it in mcp-pve.env
pveum aclmod / --tokens 'assistant@pve!assistant' --roles PVEAuditor          # read-only start
# when you want changes (start/stop/reboot) add: --roles PVEVMAdmin on the paths you choose, not on /
```

The deny list in code (see SAFETY.md) is a second wall; the Proxmox role is the first. Prefer `PVEAuditor` plus `PVEVMAdmin` on specific guests/pools over `PVEAdmin` on `/`.

## 2. Zabbix user, role, token

1. In the Zabbix UI create a user group `Assistant` with **read** access to all host groups and **read-write** only to the host groups you want it to change (it cannot create groups).
2. Create the narrow API role: `python scripts/zabbix_setup.py role --role-name "Assistant API"` (dry run), then `--apply`. The role allows every `*.get`, plus exactly: `event.acknowledge`, `host.update`, `item.update`, `trigger.update`, `usermacro.create`, `usermacro.update`, `hostgroup.massadd`, `hostgroup.massremove`. Zabbix itself then refuses deletes and creates, even if the assistant is manipulated.
3. Create the user `assistant` with that role and group, and generate an **API token** for it (Users → API tokens). It goes into `zabbix-mcp.toml` (see `deploy/zabbix-mcp.toml.example`), mode 600.
4. Optional: `python scripts/zabbix_setup.py health --host <assistant host in Zabbix> --health-url http://<assistant>:8099/health` adds an HTTP item and a High trigger so Zabbix notices when the assistant is down. `safetynet` wires Zabbix → Telegram for that case.

Environment for these scripts: `ZABBIX_URL`, `ZABBIX_TOKEN` (an administrator's token; use it once, then revoke it).

## 3. Telegram

Create a bot with @BotFather, send it one message, read your numeric chat id from `https://api.telegram.org/bot<token>/getUpdates`. The bridge answers **only that chat id**. Put both values in `telegram.env`.

## 4. Build the tool servers

```
sh scripts/build_mcp_servers.sh /opt/pve-assistant
```

This clones `mcp-pve` and `zabbix-mcp-server` at the pinned tags, **refuses if the commit differs from the pin**, builds them without install scripts, and creates the venvs. Review upstream diffs before you move a pin: these two programs hold your Proxmox token and your Zabbix token.

Optional servers: Docker (enable the API on a host you trust; it is unauthenticated unless you front it, see SAFETY), OpenWrt (`scripts/openwrt_readonly_login.sh` on the router creates a read-only login), n8n (off by default).

## 5. Configure

```
cp deploy/config.example.toml /etc/pve-assistant/config.toml        # edit: timezone, [lab], [hints], servers
for f in mcp-pve service telegram openwrt; do cp deploy/$f.env.example /etc/pve-assistant/$f.env; done
cp deploy/zabbix-mcp.toml.example /etc/pve-assistant/zabbix-mcp.toml
chown root:assistant /etc/pve-assistant/*; chmod 640 /etc/pve-assistant/*     # edit the values first
```

The two things that decide answer quality are `[lab] description` (names of your nodes, in a sentence) and `[hints]` (the names of your guests/containers, so that "is web01 running?" is routed to the right server).

## 6. Install and first run

```
python3 -m venv /opt/pve-assistant/venv && /opt/pve-assistant/venv/bin/pip install mcp==2.3.0
install -d /opt/pve-assistant/app && cp -r src/pve_assistant /opt/pve-assistant/app/
cp deploy/pve-assistant.service /etc/systemd/system/
# 1) check the gate without a model, 2) look at a real answer, 3) only then start the service
runuser -u assistant -- env PYTHONPATH=/opt/pve-assistant/app ASSISTANT_CONFIG=/etc/pve-assistant/config.toml \
    /opt/pve-assistant/venv/bin/python -m pve_assistant gate-eval
runuser -u assistant -- env PYTHONPATH=/opt/pve-assistant/app ASSISTANT_CONFIG=/etc/pve-assistant/config.toml \
    /opt/pve-assistant/venv/bin/python -m pve_assistant ask "which guests are running?"
mkdir -p /etc/systemd/system/pve-assistant.service.d
printf '[Service]\nEnvironment=ASSISTANT_DRYRUN=1\n' > /etc/systemd/system/pve-assistant.service.d/dryrun.conf    # writes are logged, not executed
systemctl daemon-reload && systemctl enable --now pve-assistant
```

Pull the model once: `ollama pull qwen3.5:4b`. The first answer is slow while the model loads; the service warms it up at start.

## 7. Go live

Run `answer-eval` (EVALUATION.md) with dry-run on, read every failing answer, then remove the drop-in and restart **when the queue is empty** (`/status` shows `Queue: 0`):

```
rm /etc/systemd/system/pve-assistant.service.d/dryrun.conf && systemctl daemon-reload && systemctl restart pve-assistant
```

Test the first write on something disposable (a throwaway container), not on a service you care about.

# Secrets and rotation

## Where they live

| Secret | File | Used by |
|---|---|---|
| Proxmox API token | `mcp-pve.env` | the Proxmox MCP child process |
| Zabbix API token | `zabbix-mcp.toml` | the Zabbix MCP child process |
| Telegram bot token + your chat id | `telegram.env` | the bridge (and, once, `zabbix_setup.py safetynet`, which copies the token into a Zabbix media type) |
| HTTP API key | `service.env` | the API |
| Router password | `openwrt.env` | the OpenWrt MCP |
| n8n MCP token | `n8n-mcp.env` | optional |

All mode 640 `root:assistant`, none in the repository (`.gitignore` excludes `*.env` and `config.toml`; CI runs `scripts/scan_repo.py`). Never paste a token into a chat, a ticket or a command line (it lands in shell history and `ps`). If you did, rotate it.

## Rotation order and checks

For each secret: **create the new one, test it, switch, then revoke the old one** – never revoke first. After every switch restart the assistant when idle and ask one question that uses that system.

1. **HTTP API key** (lowest risk – try this one first): `openssl rand -hex 24` into `service.env`; update whatever calls the API.
2. **Proxmox**: `pveum user token add assistant@pve assistant2 --privsep 1`, copy the ACL of the old token, put the new id/secret in `mcp-pve.env`, check `ask "list nodes"`, then `pveum user token remove assistant@pve assistant`.
3. **Zabbix**: Users → API tokens → create a new token for the assistant user, update `zabbix-mcp.toml`, check, delete the old token. If you used an administrator token for `scripts/zabbix_setup.py`, delete that one too.
4. **Router**: set a new password on the rpcd login (`uci set rpcd.@login[N].password="$(uhttpd -m 'NEWPASS')"; uci commit rpcd; /etc/init.d/rpcd restart`), update `openwrt.env`.
5. **Telegram**: BotFather → `/revoke` → new token into `telegram.env`; if the safety net is in use, rerun `zabbix_setup.py safetynet --apply` so the Zabbix media type gets the new token.
6. **n8n MCP**: regenerate the token in n8n's MCP settings.

## If a secret leaked into the repository

Rotating it is mandatory and sufficient for the secret. Removing it from history is optional hygiene (`git filter-repo`), and does nothing for forks or caches. Run `python scripts/scan_repo.py --values <file with the literal values, outside the repo>` before every push.

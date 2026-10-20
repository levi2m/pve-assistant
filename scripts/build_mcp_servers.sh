#!/bin/sh
# Fetch and build the two third-party MCP servers at pinned versions. Run as root on the machine that hosts the assistant.
#   sh scripts/build_mcp_servers.sh [/opt/pve-assistant]
# Needs: git, Node.js >= 20 with npm, Python >= 3.11 with venv.
# Licences: mcp-pve is MIT; zabbix-mcp-server (initMAX) is AGPL-3.0. Both are fetched, not copied into this repository, and run as separate processes.
set -eu
BASE="${1:-/opt/pve-assistant}"
mkdir -p "$BASE/src" "$BASE/servers"

# The tags and commits below are the ones this assistant was developed and tested against. Review upstream changes before moving a pin.
PVE_REPO="${MCP_PVE_REPO:-https://github.com/Samik081/mcp-pve.git}"
PVE_TAG="v0.9.0"
PVE_COMMIT="db76162"
ZBX_REPO="${ZABBIX_MCP_REPO:-https://github.com/initMAX/zabbix-mcp-server.git}"
ZBX_TAG="v1.41"
ZBX_COMMIT="ccff85d"

fetch() {  # name repo tag commit
  [ -d "$BASE/src/$1" ] || git clone "$2" "$BASE/src/$1"
  git -C "$BASE/src/$1" fetch --tags --quiet
  git -C "$BASE/src/$1" checkout --quiet "$3"
  got=$(git -C "$BASE/src/$1" rev-parse --short=7 HEAD)
  case "$got" in "$4"*) ;; *) echo "REFUSING: $1 $3 is at $got, expected $4. Review before changing the pin." >&2; exit 1;; esac
  echo "$1 $3 ($got)"
}
fetch mcp-pve "$PVE_REPO" "$PVE_TAG" "$PVE_COMMIT"
(cd "$BASE/src/mcp-pve" && npm ci --ignore-scripts && npm run build)

fetch zabbix-mcp-server "$ZBX_REPO" "$ZBX_TAG" "$ZBX_COMMIT"
python3 -m venv "$BASE/zabbix-mcp-venv"
"$BASE/zabbix-mcp-venv/bin/pip" install --quiet "$BASE/src/zabbix-mcp-server"

python3 -m venv "$BASE/venv"
"$BASE/venv/bin/pip" install --quiet "mcp==2.3.0"
cp mcp_servers/*.py "$BASE/servers/"
echo "built. Next: create the env files (deploy/*.example), config.toml, and install deploy/pve-assistant.service"

#!/bin/sh
# Run ON THE ROUTER (OpenWrt) as root:  sh openwrt_readonly_login.sh
# Creates an rpcd login 'assistant' that can call only the read-only ubus methods the bundled OpenWrt MCP server uses.
# Check first which of these your firmware has:  ubus list | grep -E "mwan3|luci-rpc|iwinfo"   (remove ACL lines for what is missing)
set -e
# BusyBox has no `read -s`, so generate the password here instead of typing one.
PW=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24)
cat > /usr/share/rpcd/acl.d/assistant-ro.json <<'JSON'
{
  "assistant-ro": {
    "description": "Read-only access for the assistant",
    "read": {
      "ubus": {
        "system": ["board", "info"],
        "network.interface": ["dump"],
        "network.device": ["status"],
        "network.wireless": ["status"],
        "luci-rpc": ["getDHCPLeases", "getHostHints"],
        "mwan3": ["status"],
        "iwinfo": ["info", "assoclist"]
      }
    }
  }
}
JSON
uci add rpcd login >/dev/null
uci set rpcd.@login[-1].username='assistant'
uci set rpcd.@login[-1].password="$(uhttpd -m "$PW")"
uci add_list rpcd.@login[-1].read='assistant-ro'
uci commit rpcd
/etc/init.d/rpcd restart
echo "done. Put these two lines into the OpenWrt server's env file (shown once, not stored here):"
echo "OPENWRT_USER=assistant"
echo "OPENWRT_PASS=$PW"

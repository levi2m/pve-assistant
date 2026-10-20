"""Minimal Zabbix JSON-RPC client for the setup scripts (standard library only).

Environment: ZABBIX_URL    base URL of the frontend, e.g. https://zabbix.example.net/zabbix
             ZABBIX_TOKEN  API token of an administrator (Users > API tokens). Never put it on a command line.
"""
import json
import os
import sys
import urllib.request


def call(method, params=None):
    url = os.environ.get("ZABBIX_URL", "").rstrip("/")
    token = os.environ.get("ZABBIX_TOKEN", "")
    if not url or not token:
        sys.exit("set ZABBIX_URL and ZABBIX_TOKEN (an administrator's API token) in the environment")
    req = urllib.request.Request(url + "/api_jsonrpc.php", json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}, "id": 1}).encode(),
                                 {"Content-Type": "application/json-rpc", "Authorization": f"Bearer {token}"})
    out = json.load(urllib.request.urlopen(req, timeout=30))
    if "error" in out:
        sys.exit(f"{method}: {out['error'].get('data') or out['error']}")
    return out["result"]

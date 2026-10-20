#!/usr/bin/env python3
"""Zabbix-side setup for the assistant. Every command is a dry run until you add --apply.

  role        create or narrow the API role the assistant's token uses: every read method, plus ONLY the writes the built-in
              recipes call (acknowledge, host/item/trigger update, macros, host-group membership). No delete, no create of hosts,
              templates, actions or users, no settings.
  health      HTTP-agent item + High trigger on an existing host that watch the assistant's /health endpoint
  safetynet   let Zabbix itself message you on Telegram when the assistant is down or a High+ problem stays unacknowledged
              (the assistant cannot do this itself: it is exactly the case where it is not running)

Environment: ZABBIX_URL, ZABBIX_TOKEN (see _zabbix_api.py).   Run:  python3 scripts/zabbix_setup.py role --role-name "Assistant API" [--apply]

The user, its user group (read on everything, read-write only on the host groups you want it to change) and its API token are created
in the Zabbix UI: there is no benefit in scripting the creation of a credential. Put the token in the assistant's zabbix server env file.
These scripts are derived from ones that ran against a real lab; this generic version has been dry-run tested only.
"""
import argparse
import sys

from _zabbix_api import call

WRITES = ["event.acknowledge", "host.update", "item.update", "trigger.update", "usermacro.create", "usermacro.update", "hostgroup.massadd", "hostgroup.massremove"]
READS = """action alert apmlog apmmetric apmspan apmtrace ceprule correlation dashboard dcheck device dhost discoveryrule discoveryruleprototype drule dservice
event graph graphitem graphprototype hanode history host hostdashboard hostgroup hostinterface hostprototype httptest iconmap image item itemprototype
maintenance map mediatype problem proxy proxygroup service sla template templatedashboard templategroup trend trigger triggerprototype usermacro valuemap""".split()


def role(a):
    want = sorted([f"{r}.get" for r in READS] + WRITES + ["apiinfo.version"])
    have = call("role.get", {"filter": {"name": [a.role_name]}, "output": ["roleid", "name", "type"], "selectRules": "extend"})
    if have:
        r = have[0]
        now = set(r["rules"]["api"])
        print(f"role '{a.role_name}' exists: {len(now)} methods, {len(want)} wanted; would remove {sorted(now - set(want))[:20]}, add {sorted(set(want) - now)[:20]}")
    else:
        print(f"role '{a.role_name}' does not exist: would create it (type Admin, {len(want)} allowed API methods; writes: {', '.join(WRITES)})")
    if not a.apply:
        return print("dry run; add --apply")
    rules = {"api.access": "1", "api.mode": "1", "api": want}
    if have:
        rules = {**have[0]["rules"], **rules}
        call("role.update", {"roleid": have[0]["roleid"], "rules": rules})
    else:
        call("role.create", {"name": a.role_name, "type": 2, "rules": rules})
    print("done. Assign the role to the assistant's Zabbix user. If Zabbix rejects a method your version does not have, remove it from READS and rerun.")


def health(a):
    host = call("host.get", {"filter": {"host": [a.host]}, "output": ["hostid"]})
    if not host:
        sys.exit(f"host '{a.host}' not found: create a host for the assistant first")
    hid, key = host[0]["hostid"], "assistant.health"
    have_item = call("item.get", {"hostids": [hid], "filter": {"key_": key}, "output": ["itemid"]})
    print(f"host {a.host}: item {key} {'exists' if have_item else 'will be created'} (HTTP agent GET {a.health_url}, every minute)")
    desc = "Assistant is down or unhealthy (chat and alerts may not work)"
    have_trigger = call("trigger.get", {"hostids": [hid], "filter": {"description": [desc]}, "output": ["triggerid"]})
    print(f"trigger '{desc}' {'exists' if have_trigger else 'will be created (High)'}")
    if not a.apply:
        return print("dry run; add --apply")
    if not have_item:
        call("item.create", {"hostid": hid, "name": "Assistant health (HTTP status of /health)", "key_": key, "type": 19, "value_type": 3, "delay": "1m",
                             "url": a.health_url, "request_method": 0, "retrieve_mode": 1, "status_codes": "200,503", "timeout": "5s",
                             "preprocessing": [{"type": "5", "params": "HTTP/[\\d.]+ (\\d+)\n\\1", "error_handler": "0", "error_handler_params": ""}],
                             "description": "200 = healthy; 503 = a tool server keeps failing; no data = the service is down."})
    if not have_trigger:
        call("trigger.create", {"description": desc, "expression": f"last(/{a.host}/{key})<>200 or nodata(/{a.host}/{key},5m)=1", "priority": 4})
    print("done")


def safetynet(a):
    """Enables a Telegram media type for the assistant's Zabbix user and two actions. The bot token is read from the environment, never printed."""
    import os
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    mt = call("mediatype.get", {"filter": {"name": [a.media_type]}, "output": "extend"})
    if not mt:
        sys.exit(f"media type '{a.media_type}' not found (import Zabbix's Telegram media type first)")
    user = call("user.get", {"filter": {"username": [a.user]}, "output": ["userid"]})
    if not user:
        sys.exit(f"user '{a.user}' not found")
    print(f"media type '{a.media_type}': will be enabled with parse mode html and the token from TELEGRAM_BOT_TOKEN ({'set' if token else 'NOT SET'}, not shown)")
    print(f"user '{a.user}': Telegram media to chat {a.chat_id}, severities High + Disaster only")
    print("actions to create (disabled actions are enabled): 'Assistant down' (trigger name contains 'Assistant is down'), 'High unacknowledged 10 minutes' (severity >= High, escalation step after 10 min)")
    if not a.apply:
        return print("dry run; add --apply")
    if not token:
        sys.exit("export TELEGRAM_BOT_TOKEN first")
    params = [{"name": p["name"], "value": token if p["name"] == "api_token" else ("html" if p["name"] == "api_parse_mode" else p["value"])} for p in mt[0]["parameters"]]
    call("mediatype.update", {"mediatypeid": mt[0]["mediatypeid"], "status": 0, "parameters": params})
    call("user.update", {"userid": user[0]["userid"], "medias": [{"mediatypeid": mt[0]["mediatypeid"], "sendto": [a.chat_id], "active": 0, "severity": 48, "period": "1-7,00:00-24:00"}]})
    print("media type and user media set. Create the two actions in Configuration > Actions (Trigger actions) with the conditions above; "
          "operations: send message to the user via the Telegram media type. Actions are left to the UI because their wording is yours to choose.")


p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
sub = p.add_subparsers(dest="cmd", required=True)
for name, fn in (("role", role), ("health", health), ("safetynet", safetynet)):
    s = sub.add_parser(name)
    s.add_argument("--apply", action="store_true")
    s.set_defaults(fn=fn)
    if name == "role":
        s.add_argument("--role-name", default="Assistant API")
    if name == "health":
        s.add_argument("--host", required=True, help="existing Zabbix host that represents the assistant")
        s.add_argument("--health-url", required=True, help="e.g. http://<assistant-ip>:8099/health")
    if name == "safetynet":
        s.add_argument("--user", required=True, help="the assistant's Zabbix user")
        s.add_argument("--chat-id", required=True)
        s.add_argument("--media-type", default="Telegram")
a = p.parse_args()
a.fn(a)

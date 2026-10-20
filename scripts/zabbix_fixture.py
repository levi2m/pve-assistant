#!/usr/bin/env python3
"""Disposable Zabbix fixture for testing the change recipes against a real server.   python3 scripts/zabbix_fixture.py setup|fire|clear|cleanup|status

Creates the host `assistant-eval-host` (in the host group named by FIXTURE_GROUPID, a group the assistant may write to) with a trapper item and two
triggers that fire when the value is >= 1. `cleanup` deletes the host and with it every item, trigger and problem.
Environment: ZABBIX_URL, ZABBIX_TOKEN (administrator), FIXTURE_GROUPID, ZABBIX_SENDER (command that runs zabbix_sender on a machine that
can reach the server, default `zabbix_sender -z 127.0.0.1`).
"""
import os
import shlex
import subprocess
import sys
import time

from _zabbix_api import call as zbx

HOST, KEY = "assistant-eval-host", "assistant.eval.value"
GROUP = os.environ.get("FIXTURE_GROUPID", "")
SENDER = shlex.split(os.environ.get("ZABBIX_SENDER", "zabbix_sender -z 127.0.0.1"))


def host_id():
    r = zbx("host.get", {"filter": {"host": [HOST]}, "output": ["hostid"]})
    return r[0]["hostid"] if r else None


def send(value, tries=20):
    """zabbix_sender exits 0 even when the item is not known yet ("processed: 0"), so read its answer and retry."""
    for _ in range(tries):
        out = subprocess.run(SENDER + ["-s", HOST, "-k", KEY, "-o", str(value)], capture_output=True, text=True).stdout
        if "processed: 1;" in out:
            return
        time.sleep(8)
    sys.exit("Zabbix never accepted the value (item still unknown?)")


cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
if cmd == "setup":
    if not GROUP:
        sys.exit("set FIXTURE_GROUPID to the id of a host group the assistant's user may write to")
    if host_id():
        sys.exit("fixture already exists (run cleanup first)")
    hid = zbx("host.create", {"host": HOST, "name": HOST, "groups": [{"groupid": GROUP}], "description": "disposable fixture for assistant tests"})["hostids"][0]
    zbx("item.create", {"hostid": hid, "name": "Assistant eval value", "key_": KEY, "type": 2, "value_type": 3})
    zbx("trigger.create", [
        {"description": "Assistant eval problem one", "expression": f"last(/{HOST}/{KEY})>=1", "priority": 2, "manual_close": 1},
        {"description": "Assistant eval problem two", "expression": f"last(/{HOST}/{KEY})>=1", "priority": 4, "manual_close": 1}])
    print("fixture created, hostid", hid)
elif cmd == "fire":
    send(1)
    time.sleep(45)
    send(1)            # the first value can land before Zabbix has loaded the triggers
    for _ in range(15):
        if len(zbx("problem.get", {"hostids": [host_id()], "output": ["eventid"]})) >= 2:
            break
        time.sleep(6)
    print("value 1 accepted; problems:", len(zbx("problem.get", {"hostids": [host_id()], "output": ["eventid"]})))
elif cmd == "clear":
    send(0)
    print("sent value 0 (problems resolve)")
elif cmd == "cleanup":
    hid = host_id()
    if hid:
        zbx("host.delete", [hid])
    print("fixture removed" if hid else "no fixture")
else:
    hid = host_id()
    print("fixture:", "absent" if not hid else f"hostid {hid}, problems {len(zbx('problem.get', {'hostids': [hid], 'output': ['eventid']}))}")

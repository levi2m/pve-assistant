"""Flat, name-based Zabbix operations built on the EXISTING Zabbix MCP tools (no second route to Zabbix).

Each recipe resolves its target by name with read tools, refuses ambiguity, makes exactly ONE write call with exact parameters,
reads the state back, and answers with 'before -> after'. The raw Zabbix write tools are hidden from the model; only recipes use them."""
import json
import re

from .config import get
from .jsonutil import _find_json

SEVERITY = {"not classified": 0, "information": 1, "warning": 2, "average": 3, "high": 4, "disaster": 5}
PROTECTED_HOSTS = set(get("zabbix", "protected_hosts", []))      # e.g. the Zabbix server host itself: never disabled by the assistant
SECRET_WORDS = re.compile(r"PASS|SECRET|TOKEN|KEY|PSK|CRED|AUTH", re.I)
MACRO_RE = re.compile(r"^\{\$[A-Z0-9_.]+(:[A-Za-z0-9_.\-/*]+)?\}$")
MAX_HOURS = 72
NEEDS = ["event_acknowledge", "problem_active_get", "problem_get", "host_get", "host_update", "item_get", "item_update", "trigger_get",
         "trigger_update", "usermacro_get", "usermacro_create", "usermacro_update", "hostgroup_get", "hostgroup_massadd", "hostgroup_massremove"]


class RecipeError(Exception):
    pass


async def zr(reg, tool, args):
    """Call a Zabbix MCP tool; raise RecipeError with its message on failure; return parsed JSON."""
    is_err, text = await reg.call_raw(tool, args)
    if is_err:
        raise RecipeError(re.sub(r"\[untrusted text[^\]]*\]", "", text).strip()[:300])
    _, data = _find_json(text)
    return data if data is not None else text


def tag(reg, message=""):
    rid = getattr(reg, "current_rid", None)
    return (message.strip() + " " if message.strip() else "") + f"[via assistant{', request ' + str(rid) if rid else ''}]"


def as_list(data, key=None):
    if isinstance(data, dict):
        data = data.get(key) if key and key in data else next((v for v in data.values() if isinstance(v, list)), [])
    return data if isinstance(data, list) else []


# ------------------------------------------------------------------ resolving names (read tools only)
async def find_host(reg, name):
    name = name.strip()
    if not name:
        raise RecipeError("Which host? Give its name.")
    for args in ({"filter": {"host": [name]}}, {"search": {"host": name}}, {"search": {"name": name}}):
        rows = as_list(await zr(reg, "host_get", {**args, "output": "extend", "limit": 20}), "hosts")
        if len(rows) == 1:
            return rows[0]
        if len(rows) > 1:
            raise RecipeError("Several hosts match: " + ", ".join(r["host"] for r in rows[:5]) + ". Use the exact name.")
    raise RecipeError(f"No host named '{name}' in Zabbix.")


async def find_in_host(reg, host, text, tool, field, label):
    rows = as_list(await zr(reg, tool, {"hostids": [host["hostid"]], "search": {field: text}, "output": "extend", "limit": 20}))
    if tool == "item_get":                                  # items can also be named by their key
        by_key = as_list(await zr(reg, tool, {"hostids": [host["hostid"]], "search": {"key_": text}, "output": "extend", "limit": 20}))
        rows += [k for k in by_key if k["itemid"] not in {x["itemid"] for x in rows}]
    exact = [r for r in rows if r.get(field, "").lower() == text.lower() or r.get("key_", "").lower() == text.lower()]
    rows = exact or rows
    if not rows:
        raise RecipeError(f"Host {host['host']} has no {label} matching '{text}'.")
    if len(rows) > 1:
        raise RecipeError(f"Several {label}s on {host['host']} match: " + "; ".join(r.get(field, "")[:50] for r in rows[:5]) + ". Be more specific.")
    return rows[0]


async def find_problem(reg, text):
    text = text.strip()
    if not text:
        raise RecipeError("Which problem? Give part of its name, the host, or the event id.")
    if text.isdigit():
        rows = as_list(await zr(reg, "problem_get", {"eventids": [text], "output": "extend"}))
        if rows:
            return {"eventid": text, "name": rows[0].get("name", ""), "host": "?", "acknowledged": rows[0].get("acknowledged"), "suppressed": rows[0].get("suppressed")}
        raise RecipeError(f"No open problem with event id {text}.")
    words = [w for w in re.split(r"\W+", text.lower()) if w]
    active = as_list(await zr(reg, "problem_active_get", {"limit": 200}), "problems")
    hits = [p for p in active if all(w in f"{p.get('host', '')} {p.get('name', '')}".lower() for w in words)]
    if not hits:                                            # information-level and others the 'active' view hides
        raw = as_list(await zr(reg, "problem_get", {"search": {"name": text}, "limit": 20, "output": "extend"}))
        hits = [{"eventid": p["eventid"], "name": p.get("name", ""), "host": "?", "acknowledged": p.get("acknowledged"), "suppressed": p.get("suppressed")} for p in raw]
    if not hits:
        raise RecipeError(f"No open problem matches '{text}'.")
    if len(hits) > 1:
        raise RecipeError(f"{len(hits)} problems match '{text}'. Which one? " + " | ".join(f"event {p['eventid']}: {p.get('host', '?')}: {p['name'][:60]}" for p in hits[:5]) + ". Say the event id or more of the name.")
    return hits[0]


async def problem_state(reg, eventid):
    """Zabbix hides suppressed (snoozed) problems from the default query, so look there too."""
    for extra in ({}, {"suppressed": True}):
        rows = as_list(await zr(reg, "problem_get", {"eventids": [eventid], "output": "extend", **extra}))
        if rows:
            return rows[0]
    return None


def label(p):
    return f"'{p['name'][:70]}'" + (f" on {p['host']}" if p.get("host") not in (None, "?") else "") + f" (event {p['eventid']})"


def yn(v, yes, no):
    return yes if str(v) == "1" else no


# ------------------------------------------------------------------ problem recipes
async def _ack_action(reg, text, action, message, verify, what):
    p = await find_problem(reg, text)
    before = await problem_state(reg, p["eventid"])
    await zr(reg, "event_acknowledge", {"eventids": [p["eventid"]], "action": action, "message": tag(reg, message)})
    after = await problem_state(reg, p["eventid"])
    ok, detail = verify(before, after)
    if not ok:
        raise RecipeError(f"Zabbix accepted the request for {label(p)} but the read-back does not show it ({detail}).")
    return f"{what} {label(p)}. {detail}", p


async def ack_problem(reg, a):
    msg = a.get("message", "")
    p0 = await find_problem(reg, a.get("problem", ""))
    cur = await problem_state(reg, p0["eventid"])
    if cur and cur.get("acknowledged") == "1" and not msg.strip():
        return False, f"{label(p0)} is already acknowledged."
    out, _ = await _ack_action(reg, a.get("problem", ""), 2 | (4 if msg.strip() else 0), msg,
                               lambda b, c: (bool(c) and c.get("acknowledged") == "1", f"acknowledged: {yn(b and b.get('acknowledged'), 'yes', 'no')} -> {yn(c and c.get('acknowledged'), 'yes', 'no')}"), "Acknowledged")
    return False, out


async def comment_problem(reg, a):
    msg = a.get("message", "").strip()
    if not msg:
        raise RecipeError("What should the comment say?")
    out, _ = await _ack_action(reg, a.get("problem", ""), 4, msg, lambda b, c: (bool(c), "comment added"), "Commented on")
    return False, out


async def set_problem_severity(reg, a):
    sev = str(a.get("severity", "")).lower()
    if sev not in SEVERITY:
        raise RecipeError(f"Severity must be one of: {', '.join(SEVERITY)}.")
    p = await find_problem(reg, a.get("problem", ""))
    before = await problem_state(reg, p["eventid"])
    await zr(reg, "event_acknowledge", {"eventids": [p["eventid"]], "action": 8, "severity": SEVERITY[sev], "message": tag(reg)})
    after = await problem_state(reg, p["eventid"])
    names = {v: k for k, v in SEVERITY.items()}
    if not after or int(after.get("severity", -1)) != SEVERITY[sev]:
        raise RecipeError(f"Zabbix accepted the change for {label(p)} but the read-back still shows severity {names.get(int((after or {}).get('severity', -1)), '?')}.")
    return False, f"Severity of {label(p)}: {names.get(int(before.get('severity', -1)), '?') if before else '?'} -> {sev}."


async def close_problem(reg, a):
    msg = a.get("message", "")
    p = await find_problem(reg, a.get("problem", ""))
    try:
        await zr(reg, "event_acknowledge", {"eventids": [p["eventid"]], "action": 1 | (4 if msg.strip() else 0), "message": tag(reg, msg)})
    except RecipeError as e:
        raise RecipeError(f"Could not close {label(p)}: {e} (the trigger may not allow manual close; Zabbix closes it by itself when the condition clears).") from None
    after = await problem_state(reg, p["eventid"])
    if after and after.get("r_eventid", "0") == "0" and after.get("acknowledged") != "1":
        raise RecipeError(f"Zabbix accepted the close request for {label(p)} but it still shows as open (it closes on the next check).")
    return False, f"Closed {label(p)}."


# ------------------------------------------------------------------ enable / disable (with automatic undo)
def hours_of(a):
    h = int(a.get("hours") or 4)
    if not 1 <= h <= MAX_HOURS:
        raise RecipeError(f"Hours must be between 1 and {MAX_HOURS}.")
    return h


async def _toggle(reg, kind, host_name, text, enabled, hours, a):
    host = await find_host(reg, host_name)
    if kind == "host":
        obj, id_key, name, tool, getter = host, "hostid", host["host"], "host_update", None
        lab = f"host {host['host']}"
        if not enabled and host["host"] in PROTECTED_HOSTS:
            raise RecipeError(f"{host['host']} is protected: it monitors the monitoring itself and is never disabled by the agent.")
    elif kind == "item":
        obj = await find_in_host(reg, host, text, "item_get", "name", "item")
        id_key, name, tool = "itemid", obj["name"], "item_update"
        lab = f"item {host['host']}/{obj['key_']}"
    else:
        obj = await find_in_host(reg, host, text, "trigger_get", "description", "trigger")
        id_key, name, tool = "triggerid", obj["description"], "trigger_update"
        lab = f"trigger {host['host']}/{obj['description'][:40]}"
    want = "0" if enabled else "1"
    if str(obj.get("status")) == want:
        return False, f"{kind.capitalize()} {name} is already {'enabled' if enabled else 'disabled'}."
    await zr(reg, tool, {"params": {id_key: obj[id_key], "status": int(want)}})
    fresh = as_list(await zr(reg, {"host": "host_get", "item": "item_get", "trigger": "trigger_get"}[kind], {f"{kind}ids": [obj[id_key]], "output": "extend"}))
    if not fresh or str(fresh[0].get("status")) != want:
        raise RecipeError(f"Zabbix accepted the change for {kind} {name} but the read-back does not show it.")
    rid = getattr(reg, "current_rid", None)
    if enabled:
        n = reg.timers.cancel(f"enable_{kind}", lab)
        return False, f"Enabled {kind} {name} on {host['host']} (disabled -> enabled)." + (" Cancelled its automatic re-enable timer." if n else "")
    reg.timers.add(f"enable_{kind}", lab, {id_key: obj[id_key], "name": name, "host": host["host"]}, hours, rid)
    return False, f"Disabled {kind} {name} on {host['host']} (enabled -> disabled). It will be enabled again automatically in {hours} h."


async def set_host_enabled(reg, a):
    return await _toggle(reg, "host", a.get("host", ""), "", bool(a.get("enabled")), None if a.get("enabled") else hours_of(a), a)


async def set_item_enabled(reg, a):
    return await _toggle(reg, "item", a.get("host", ""), a.get("item", ""), bool(a.get("enabled")), None if a.get("enabled") else hours_of(a), a)


async def set_trigger_enabled(reg, a):
    return await _toggle(reg, "trigger", a.get("host", ""), a.get("trigger", ""), bool(a.get("enabled")), None if a.get("enabled") else hours_of(a), a)


async def set_trigger_severity(reg, a):
    sev = str(a.get("severity", "")).lower()
    if sev not in SEVERITY:
        raise RecipeError(f"Severity must be one of: {', '.join(SEVERITY)}.")
    host = await find_host(reg, a.get("host", ""))
    trg = await find_in_host(reg, host, a.get("trigger", ""), "trigger_get", "description", "trigger")
    names = {v: k for k, v in SEVERITY.items()}
    old = int(trg.get("priority", -1))
    if old == SEVERITY[sev]:
        return False, f"Trigger '{trg['description'][:60]}' on {host['host']} is already {sev}."
    await zr(reg, "trigger_update", {"params": {"triggerid": trg["triggerid"], "priority": SEVERITY[sev]}})
    fresh = as_list(await zr(reg, "trigger_get", {"triggerids": [trg["triggerid"]], "output": "extend"}))
    if not fresh or int(fresh[0].get("priority", -1)) != SEVERITY[sev]:
        raise RecipeError("Zabbix accepted the change but the read-back does not show it.")
    return False, f"Trigger '{trg['description'][:60]}' on {host['host']}: {names.get(old, '?')} -> {sev}. (Permanent; to undo, set it back to {names.get(old, '?')}.)"


# ------------------------------------------------------------------ macros and groups
async def set_host_macro(reg, a):
    macro, value = a.get("macro", "").strip(), str(a.get("value", ""))
    if not MACRO_RE.match(macro):
        raise RecipeError("A macro looks like {$NAME} (capitals, digits, underscore, dot).")
    if SECRET_WORDS.search(macro):
        raise RecipeError("Macros whose name suggests a secret (password, token, key...) are not set by the agent.")
    if len(value) > 255:
        raise RecipeError("Value too long (255 characters max).")
    host = await find_host(reg, a.get("host", ""))
    rows = as_list(await zr(reg, "usermacro_get", {"hostids": [host["hostid"]], "filter": {"macro": [macro]}, "output": "extend"}))
    if rows:
        cur = rows[0]
        if str(cur.get("type", "0")) != "0":
            raise RecipeError(f"{macro} on {host['host']} is a secret or vault macro: not changed.")
        if cur.get("value") == value:
            return False, f"{macro} on {host['host']} is already '{value}'."
        await zr(reg, "usermacro_update", {"params": {"hostmacroid": cur["hostmacroid"], "value": value}})
        old = cur.get("value")
    else:
        await zr(reg, "usermacro_create", {"params": {"hostid": host["hostid"], "macro": macro, "value": value}})
        old = None
    fresh = as_list(await zr(reg, "usermacro_get", {"hostids": [host["hostid"]], "filter": {"macro": [macro]}, "output": "extend"}))
    if not fresh or fresh[0].get("value") != value:
        raise RecipeError("Zabbix accepted the change but the read-back does not show it.")
    return False, f"{macro} on {host['host']}: {('was ' + repr(old)) if old is not None else 'created'} -> '{value}'."


async def host_group(reg, a):
    act = str(a.get("action", "")).lower()
    if act not in ("add", "remove"):
        raise RecipeError("Action must be 'add' or 'remove'.")
    host = await find_host(reg, a.get("host", ""))
    groups = as_list(await zr(reg, "hostgroup_get", {"filter": {"name": [a.get("group", "").strip()]}, "output": "extend"}))
    if not groups:
        raise RecipeError(f"No host group named '{a.get('group', '')}'. (The assistant cannot create groups.)")
    grp = groups[0]
    mine = as_list(await zr(reg, "hostgroup_get", {"hostids": [host["hostid"]], "output": "extend"}))
    member = any(g["groupid"] == grp["groupid"] for g in mine)
    if act == "add":
        if member:
            return False, f"{host['host']} is already in group {grp['name']}."
        await zr(reg, "hostgroup_massadd", {"params": {"groups": [{"groupid": grp["groupid"]}], "hosts": [{"hostid": host["hostid"]}]}})
    else:
        if not member:
            return False, f"{host['host']} is not in group {grp['name']}."
        if len(mine) == 1:
            raise RecipeError(f"{grp['name']} is the only group of {host['host']}; a host must keep at least one group.")
        await zr(reg, "hostgroup_massremove", {"params": {"groupids": [grp["groupid"]], "hostids": [host["hostid"]]}})
    now = as_list(await zr(reg, "hostgroup_get", {"hostids": [host["hostid"]], "output": "extend"}))
    if any(g["groupid"] == grp["groupid"] for g in now) != (act == "add"):
        raise RecipeError("Zabbix accepted the change but the read-back does not show it.")
    return False, f"{host['host']} {'added to' if act == 'add' else 'removed from'} group {grp['name']}. Groups now: {', '.join(g['name'] for g in now)}."


# ------------------------------------------------------------------ timers firing (called by the service scheduler)
async def run_timer(reg, kind, args):
    tool, id_key = {"enable_host": ("host_update", "hostid"), "enable_item": ("item_update", "itemid"), "enable_trigger": ("trigger_update", "triggerid")}[kind]
    await zr(reg, tool, {"params": {id_key: args[id_key], "status": 0}})
    return f"Re-enabled {kind.split('_')[1]} {args.get('name', '')} on {args.get('host', '?')} (the time you set ran out)."


# ------------------------------------------------------------------ the registry of virtual tools
def _wrap(fn):
    async def handler(reg, args):
        try:
            return await fn(reg, args)
        except RecipeError as e:
            return True, str(e)
        except (ValueError, TypeError) as e:
            return True, f"Invalid argument: {e}"
    return handler


def _schema(props, required):
    return {"type": "object", "properties": props, "required": required}


_P = {"type": "string", "description": "part of the problem name, the host, or the event id"}
_SEV = {"type": "string", "enum": list(SEVERITY)}
_H = {"type": "integer", "description": f"hours until it is undone automatically (1-{MAX_HOURS}, default 4)"}
RECIPES = {
    "zabbix_ack_problem": ("Acknowledge one open Zabbix problem, optionally with a message.", _schema({"problem": _P, "message": {"type": "string"}}, ["problem"]), _wrap(ack_problem)),
    "zabbix_comment_problem": ("Add a comment to one open Zabbix problem.", _schema({"problem": _P, "message": {"type": "string"}}, ["problem", "message"]), _wrap(comment_problem)),
    "zabbix_set_problem_severity": ("Change the severity of one open Zabbix problem.", _schema({"problem": _P, "severity": _SEV}, ["problem", "severity"]), _wrap(set_problem_severity)),
    "zabbix_close_problem": ("Close one open Zabbix problem manually (only if its trigger allows it).", _schema({"problem": _P, "message": {"type": "string"}}, ["problem"]), _wrap(close_problem)),
    "zabbix_set_host_enabled": ("Enable or disable monitoring of one Zabbix host; a disabled host is enabled again automatically.", _schema({"host": {"type": "string"}, "enabled": {"type": "boolean"}, "hours": _H}, ["host", "enabled"]), _wrap(set_host_enabled)),
    "zabbix_set_item_enabled": ("Enable or disable one item of a Zabbix host; re-enabled automatically.", _schema({"host": {"type": "string"}, "item": {"type": "string", "description": "item name or key"}, "enabled": {"type": "boolean"}, "hours": _H}, ["host", "item", "enabled"]), _wrap(set_item_enabled)),
    "zabbix_set_trigger_enabled": ("Enable or disable one trigger of a Zabbix host; re-enabled automatically.", _schema({"host": {"type": "string"}, "trigger": {"type": "string", "description": "trigger name"}, "enabled": {"type": "boolean"}, "hours": _H}, ["host", "trigger", "enabled"]), _wrap(set_trigger_enabled)),
    "zabbix_set_trigger_severity": ("Change the severity of one trigger of a Zabbix host.", _schema({"host": {"type": "string"}, "trigger": {"type": "string"}, "severity": _SEV}, ["host", "trigger", "severity"]), _wrap(set_trigger_severity)),
    "zabbix_set_host_macro": ("Set a non-secret user macro such as {$NAME} on one Zabbix host.", _schema({"host": {"type": "string"}, "macro": {"type": "string", "description": "like {$NAME}"}, "value": {"type": "string"}}, ["host", "macro", "value"]), _wrap(set_host_macro)),
    "zabbix_host_group": ("Add a Zabbix host to an existing host group, or remove it from one.", _schema({"host": {"type": "string"}, "group": {"type": "string"}, "action": {"type": "string", "enum": ["add", "remove"]}}, ["host", "group", "action"]), _wrap(host_group)),
}

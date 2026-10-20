"""The gate: pick a few relevant tools per request, slim their schemas, and enforce the hard deny rules in code."""
import math
import re
from collections import Counter

from .config import CFG, MAX_TOOLS, PER_SERVER_CAP

STOP = set("a an the of to in on for and or is are was were be do does did i me my you your it its this that what which who how why when where there any all can could would should please show tell give get with from at by as about has have had not no yes".split())
DELETE_WORDS = re.compile(r"\b(delete|destroy|erase|wipe|purge|remove|drop)\b", re.I)
DELETE_TOOLS = re.compile(r"delete|remove|destroy|erase|purge|prune|wipe", re.I)
DELETE_ONLY_IF = {"zabbix_host_group": r"\bgroup"}           # tools that remove a link, offered for 'remove/delete' only when the question names that link
ACTION = re.compile(r"\b(ack|comment|start|stop|restart|reboot|shut ?down|power|create|add|update|change|set|delete|remove|acknowledge|ack|close|enable|disable|pull|load|unload|run|execute|trigger|publish|unpublish|migrate|clone|suspend|resume|fix|apply|upgrade|rename|move)\b", re.I)
SERVER_HINTS = {
    "pve": r"proxmox|(?<![\d.])[1-9]\d{2}(?![\d.])|\bpve\b|lxc|\bct\b|\bvm\b|vmid|guest|\bnode\b|storage|snapshot|cluster|\bha\b|firewall|task",
    "zabbix": r"zabbix|macro|\back\b|acknowledg|severity|problem|alert|trigger|monitor|host ?group|maintenance|\bitem\b|template|event|dashboard|history|sla",
    "docker": r"docker|image|compose",
    "ollama": r"ollama|\bmodel|\bllm\b|open ?webui|qwen|loaded in memory",
    "openwrt": r"\b\d{1,3}(\.\d{1,3}){3}\b|router|openwrt|wi-?fi|wlan|\bwan\b|mwan|dhcp|\blan\b|gateway|wireless|mac address",
    "n8n": r"n8n|workflow|automation|execution|data ?table|(n8n|ai) agent",
}
HINT_BONUS = 3.0

# Lab-specific names (guest, container and host names) come from [hints] in the config file, one regex fragment per server.
for _server, _extra in CFG.get("hints", {}).items():
    if _server in SERVER_HINTS and _extra:
        SERVER_HINTS[_server] += "|" + _extra


def stem(w):
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


# plain words people use -> words the tool names and descriptions use
EXPAND = {
    "running": ["status", "state"], "healthy": ["health", "status", "inspect"], "health": ["status", "inspect"],
    "online": ["status"], "offline": ["status"], "up": ["status"], "down": ["status"], "alive": ["status"],
    "list": ["search"], "find": ["search"], "called": ["search"], "named": ["search"], "lookup": ["search"], "uptime": ["status", "info"], "memory": ["status"], "ram": ["status", "memory"],
    "space": ["storage", "disk"], "full": ["storage", "disk", "usage"], "log": ["logs"],
    "many": ["search", "list"], "exist": ["search"], "address": ["interface", "ipv4"], "ip": ["interface", "ipv4"], "guest": ["lxc", "qemu", "resource", "container"], "wifi": ["wireless", "client"], "connected": ["client", "host"], "device": ["host"], "version": ["version", "info"],
}


def toks(text):
    out = [stem(w) for w in re.findall(r"[a-z0-9]+", text.lower().replace("_", " ")) if w not in STOP and len(w) > 1]
    return out


# rare words that would outrank the tool we want: swap them for the plain word instead of adding to them
REPLACE = {w: ["problem", "active"] for w in ("critical", "major", "severe", "urgent", "issue", "wrong", "broken", "failing")}


def expand(words):
    out = []
    for w in words:
        out += REPLACE.get(w, [w])
    return out + [e for w in words for e in EXPAND.get(w, ())]


class BM25:
    def __init__(self, docs, k1=1.4, b=0.75):
        self.docs = [Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avg = sum(self.len) / max(len(docs), 1)
        df = Counter(t for d in self.docs for t in d)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.k1, self.b = k1, b

    def score(self, q, i):
        d, s = self.docs[i], 0.0
        for t in set(q):
            f = d.get(t, 0)
            if f:
                s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
        return s


# Tools that make a 4B model pick the wrong thing for plain questions: only offered when the question asks for them
HIDDEN_UNLESS = {
    "get_instance_context": r"instance|overview|context|summary",
    "get_instance_activity": r"activity|recent(ly)? (created|changed|published)|audit",
    "expand_instance_activity": r"activity|audit",
    "get_node_usage": r"node types?|which nodes",
    "list_n8n_gateway_services": r"gateway|credits",
    "health_check": r"health|version|api|server status|is zabbix (up|running|ok)",
}


# ---------------------------------------------------------------- deny rules (code, not prompt)
DENY_NAMES = {
    # never delete guests or storage (also their snapshots: deleting or rolling back destroys guest data)
    "pve_delete_qemu_vm": "guests are never deleted", "pve_delete_lxc_container": "guests are never deleted",
    "pve_delete_storage": "storage is never deleted",
    "pve_delete_qemu_snapshot": "snapshots are never deleted", "pve_delete_lxc_snapshot": "snapshots are never deleted",
    "pve_rollback_qemu_snapshot": "snapshot rollback is not allowed", "pve_rollback_lxc_snapshot": "snapshot rollback is not allowed",
    # HA is not used on this lab and a 4B model has no business toggling it (found by the injection test)
    "pve_disarm_ha": "HA control is off limits", "pve_arm_ha": "HA control is off limits",
    "pve_create_ha_resource": "HA control is off limits", "pve_update_ha_resource": "HA control is off limits", "pve_delete_ha_resource": "HA control is off limits",
    "pve_create_ha_rule": "HA control is off limits", "pve_update_ha_rule": "HA control is off limits", "pve_delete_ha_rule": "HA control is off limits",
    # identity and access, network and firewall, cluster-wide settings, mass actions: no job for a 4B model
    **{n: "identity and access changes are off limits" for n in (
        "pve_create_user", "pve_update_user", "pve_delete_user", "pve_update_acl",
        "pve_create_user_token", "pve_update_user_token", "pve_delete_user_token")},
    **{n: "network, firewall and cluster settings are off limits" for n in (
        "pve_create_network", "pve_update_network", "pve_delete_network",
        "pve_update_firewall_options", "pve_create_firewall_rule", "pve_update_firewall_rule", "pve_delete_firewall_rule",
        "pve_update_cluster_options")},
    **{n: "storage configuration and pools are off limits" for n in (
        "pve_create_storage", "pve_update_storage", "pve_create_pool", "pve_update_pool", "pve_delete_pool")},
    **{n: "mass actions are off limits; act on one guest at a time" for n in (
        "pve_bulk_start_guests", "pve_bulk_shutdown_guests", "pve_bulk_suspend_guests", "pve_bulk_migrate_guests")},
    "pve_manage_node_service": "node services are off limits", "pve_stop_task": "stopping tasks is off limits",
    # never touch backups (reads stay allowed)
    "pve_run_backup": "backups are managed by the owner", "pve_create_backup_job": "backups are managed by the owner",
    "pve_delete_backup_job": "backups are managed by the owner",
}
N8N_GUARDED = {"execute_workflow", "test_workflow", "publish_workflow", "unpublish_workflow", "update_workflow", "archive_workflow", "restore_workflow_version"}


def check_call(tool, args, protected_workflow_ids=frozenset()):
    """Return None if allowed, else the reason it is refused."""
    if tool.name in DENY_NAMES:
        return DENY_NAMES[tool.name]
    if tool.server == "zabbix" and not tool.read_only and not getattr(tool, "virtual", False):
        return "Zabbix is changed only through the zabbix_* operations, not the raw API tools"
    if tool.server == "n8n" and tool.name in N8N_GUARDED:
        wid = str(args.get("workflowId") or args.get("workflow_id") or args.get("id") or "")
        if wid in protected_workflow_ids:
            return "protected workflows are off limits"
    return None


# ---------------------------------------------------------------- schema slimming
def _first_sentence(text, limit):
    text = " ".join((text or "").split())
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    s = m.group(1) if m else text
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _slim_prop(p):
    if "anyOf" in p:
        non_null = [x for x in p["anyOf"] if x.get("type") != "null"]
        p = {**(non_null[0] if non_null else {}), **{k: v for k, v in p.items() if k in ("description", "enum")}}
    out = {"type": p.get("type", "string")}
    if "enum" in p:
        out["enum"] = p["enum"][:12]
    if p.get("type") == "array":
        out["items"] = {"type": (p.get("items") or {}).get("type", "string")}
    if p.get("description"):
        out["description"] = _first_sentence(p["description"], 70)
    return out


# optional arguments a 4B model fills in wrongly and that a one-server lab never needs (the tool defaults work)
SLIM_DROP = {"limit", "severities", "severity_min", "min_severity", "server", "output", "raw_json", "extra_params", "searchByAny", "searchWildcardsEnabled", "sortfield", "sortorder"}


def slim(key, tool, max_optional=5):
    props = (tool.schema or {}).get("properties", {})
    required = [r for r in (tool.schema or {}).get("required", []) if r in props]
    optional = [k for k in props if k not in required and k not in SLIM_DROP][:max_optional]
    return {"type": "function", "function": {
        "name": key, "description": _first_sentence(tool.description, 170),
        "parameters": {"type": "object", "properties": {k: _slim_prop(props[k]) for k in required + optional}, "required": required}}}


# ---------------------------------------------------------------- selection
class Gate:
    def __init__(self, tools):
        self.keys = list(tools)
        self.tools = tools
        docs = []
        for k in self.keys:
            t = tools[k]
            docs.append(toks(t.name) * 3 + toks(t.description)[:60] + toks(t.server))
        self.bm25 = BM25(docs)

    def select(self, question, k=MAX_TOOLS, action_from=None):
        q = expand(toks(question))
        wants_action = bool(ACTION.search(action_from or question))
        hinted = {s for s, rx in SERVER_HINTS.items() if re.search(rx, question, re.I)}
        # a bare 3-digit id is a Proxmox guest id: it rules Docker out unless Docker is named
        if re.search(r"(?<![\d.])[1-9]\d{2}(?![\d.])", question) and not re.search(SERVER_HINTS["docker"], question, re.I):
            hinted.discard("docker")
        scored = []
        for i, key in enumerate(self.keys):
            t = self.tools[key]
            if not wants_action and not t.read_only:
                continue
            # "delete X": offer only tools that really delete or remove, never a look-alike (stop, disable) the model could substitute
            if wants_action and not t.read_only and DELETE_WORDS.search(action_from or question) and not (
                    (t.name in DELETE_ONLY_IF and re.search(DELETE_ONLY_IF[t.name], action_from or question, re.I))
                    or (t.name not in DELETE_ONLY_IF and DELETE_TOOLS.search(t.name + " " + t.description))):
                continue
            if hinted and t.server not in hinted:
                continue                 # the question names a server: do not tempt the model with the others
            if t.name in DENY_NAMES or (t.server == "zabbix" and not t.read_only and not getattr(t, "virtual", False)):
                continue
            if t.name in HIDDEN_UNLESS and not re.search(HIDDEN_UNLESS[t.name], question, re.I):
                continue
            s = self.bm25.score(q, i)
            if t.server in hinted:
                s += HINT_BONUS          # a named server's tools stay candidates even when no word matches
            if wants_action and not t.read_only and s > 0:
                s += 1.5                 # the user asked for a change: prefer the tool that makes it over generic readers
            if s > 0:
                scored.append((s, key))
        scored.sort(reverse=True)
        picked, per = [], Counter()
        cap = PER_SERVER_CAP if len(hinted) < 2 else max(3, k // len(hinted))
        for s, key in scored:
            srv = self.tools[key].server
            if per[srv] >= cap:
                continue
            picked.append(key)
            per[srv] += 1
            if len(picked) == k:
                break
        return picked, sorted(hinted), wants_action

"""The daily review. Deterministic on purpose: the numbers, the lab changes and the failures come from code, not from the 4B model.

1. read the day's transcripts            -> usage, failures, slow answers, repeated questions, corrections
2. snapshot the lab with READ tools      -> compare with the previous snapshot: what changed or appeared
3. write proposals                       -> memory notes (wait for /approve) and eval-case candidates (a file for the owner)
4. send one digest on Telegram

It never edits code, the deny list, the gate or the prompt. Memory only grows when the owner approves a note."""
import collections
import datetime
import json
import os
import re
import time


from .config import REVIEW_HOUR, STATE_DIR, TZ
from .jsonutil import _find_json
from .transcript import local_day, purge_old, read_day

SNAP_DIR = os.path.join(STATE_DIR, "snapshots")
DIGEST_DIR = os.path.join(STATE_DIR, "digests")
CANDIDATES = os.path.join(STATE_DIR, "eval-candidates.jsonl")
STATE_FILE = os.path.join(STATE_DIR, "nightly-last")
FAIL_TEXT = re.compile(r"could not|cannot|can't|unable|do not know|don't know|not available|failed", re.I)
CORRECTION = re.compile(r"^\s*(no[,.! ]|nope|wrong|that'?s (wrong|not (right|correct|true))|incorrect|not (right|correct|true)|you are wrong|you're wrong)", re.I)


# ------------------------------------------------------------------ snapshot of the lab (read tools only)
async def _json(reg, tool, args=None):
    try:
        is_err, text = await reg.call(tool, args or {})
        if is_err:
            return None
        _, data = _find_json(text)
        return data if data is not None else text
    except Exception:  # noqa: BLE001
        return None


async def snapshot(reg):
    s = {"ts": time.time(), "day": local_day()}
    res = await _json(reg, "pve_list_cluster_resources", {"type": "vm"})
    s["guests"] = {g["name"]: {"vmid": g.get("vmid"), "status": g.get("status")} for g in res if isinstance(g, dict)} if isinstance(res, list) else None
    dk = await _json(reg, "docker_list_containers")
    s["docker"] = {c["name"]: c["state"] for c in dk} if isinstance(dk, list) else None
    om = await _json(reg, "ollama_list_models")
    s["models"] = sorted(m["name"] for m in om) if isinstance(om, list) else None
    kh = await _json(reg, "openwrt_known_hosts")
    s["lan"] = {m["mac"]: {"name": m.get("name"), "ipv4": m.get("ipv4", [])} for m in kh} if isinstance(kh, list) else None
    zh = await _json(reg, "host_get", {})
    s["zabbix_hosts"] = sorted(h.get("host") for h in zh) if isinstance(zh, list) else None
    pa = await _json(reg, "problem_active_get")
    if isinstance(pa, dict):
        probs = pa.get("problems", [])
        s["problems"] = {"total": pa.get("count", len(probs)), "by_severity": dict(collections.Counter(p.get("severity_label") for p in probs)),
                         "names": sorted(p.get("name", "")[:80] for p in probs)}
    wf = await _json(reg, "search_workflows")
    s["workflows"] = len(wf.get("data", [])) if isinstance(wf, dict) else None
    s["versions"] = {}
    nodes = await _json(reg, "pve_list_nodes")
    node = (nodes[0].get("node") or nodes[0].get("name")) if isinstance(nodes, list) and nodes and isinstance(nodes[0], dict) else ""
    for key, tool, pick in (("proxmox", "pve_get_node_version", lambda d: d.get("version") if isinstance(d, dict) else None),
                            ("docker", "docker_info", lambda d: d.get("ServerVersion") if isinstance(d, dict) else None),
                            ("ollama", "ollama_version", lambda d: d if isinstance(d, str) else None),
                            ("openwrt", "openwrt_board", lambda d: (d.get("release") or {}).get("version") if isinstance(d, dict) else None)):
        arg = {"node": node} if tool == "pve_get_node_version" else {}
        s["versions"][key] = pick(await _json(reg, tool, arg))
    return s


def diff(prev, cur):
    """Human lines describing what changed between two snapshots. Missing data on either side is skipped, never reported as a change."""
    out = []

    def both(key):
        return prev.get(key) is not None and cur.get(key) is not None

    if both("guests"):
        for n, g in cur["guests"].items():
            if n not in prev["guests"]:
                out.append(f"New Proxmox guest: {n} (vmid {g['vmid']}, {g['status']})")
            elif prev["guests"][n]["status"] != g["status"]:
                out.append(f"Guest {n}: {prev['guests'][n]['status']} -> {g['status']}")
        out += [f"Guest gone: {n}" for n in prev["guests"] if n not in cur["guests"]]
    if both("docker"):
        for n, st in cur["docker"].items():
            if n not in prev["docker"]:
                out.append(f"New Docker container: {n} ({st})")
            elif prev["docker"][n] != st:
                out.append(f"Docker {n}: {prev['docker'][n]} -> {st}")
        out += [f"Docker container gone: {n}" for n in prev["docker"] if n not in cur["docker"]]
    if both("models"):
        out += [f"Ollama model added: {m}" for m in cur["models"] if m not in prev["models"]]
        out += [f"Ollama model removed: {m}" for m in prev["models"] if m not in cur["models"]]
    if both("lan"):
        for mac, d in cur["lan"].items():
            if mac not in prev["lan"]:
                out.append(f"New device seen by the router: {d.get('name') or 'unnamed'} {', '.join(d.get('ipv4') or []) or 'no IPv4'} ({mac})")
    if both("zabbix_hosts"):
        out += [f"Zabbix host added: {h}" for h in cur["zabbix_hosts"] if h not in prev["zabbix_hosts"]]
        out += [f"Zabbix host removed: {h}" for h in prev["zabbix_hosts"] if h not in cur["zabbix_hosts"]]
    if both("problems") and prev["problems"]["total"] != cur["problems"]["total"]:
        new = [n for n in cur["problems"]["names"] if n not in prev["problems"]["names"]]
        out.append(f"Zabbix active problems: {prev['problems']['total']} -> {cur['problems']['total']}" + (f"; new: {'; '.join(new[:4])}" if new else ""))
    if both("workflows") and prev["workflows"] != cur["workflows"]:
        out.append(f"n8n workflows: {prev['workflows']} -> {cur['workflows']}")
    for k, v in (cur.get("versions") or {}).items():
        old = (prev.get("versions") or {}).get(k)
        if v and old and v != old:
            out.append(f"{k} version: {old} -> {v}")
    return out


# ------------------------------------------------------------------ what happened today
def analyse(rows):
    st = {"n": len(rows), "channels": dict(collections.Counter(r["channel"] for r in rows if r["channel"] != "eval"))}
    rows = [r for r in rows if r["channel"] != "eval"]
    st["n"] = len(rows)
    secs = [r.get("seconds", 0) for r in rows]
    st["avg_s"], st["max_s"] = (round(sum(secs) / len(secs)), round(max(secs))) if secs else (0, 0)
    tools = collections.Counter(e["name"] for r in rows for e in r["events"] if e["kind"] == "tool")
    st["top_tools"] = tools.most_common(5)
    st["failures"], st["corrections"], st["slow"] = [], [], []
    seen = collections.Counter(re.sub(r"\W+", " ", r["question"].lower()).strip() for r in rows)
    st["repeated"] = [(q, n) for q, n in seen.items() if n >= 2 and q][:5]
    prev_by_channel = {}
    for r in rows:
        tool_events = [e for e in r["events"] if e["kind"] == "tool"]
        bad = [e for e in tool_events if not e["ok"] and e["note"] in ("bad args", "exception", "not offered", "")]
        why = []
        if r["outcome"] != "ok":
            why.append(r["outcome"])
        if bad:
            why.append("tool errors: " + ", ".join(sorted({e["name"] for e in bad})))
        if FAIL_TEXT.search(r.get("answer", "")):
            why.append("answer admits failure")
        if not tool_events and any(e["kind"] == "gate" and e["offered"] for e in r["events"]) and re.search(r"\b(how many|which|what|is|are|list|show)\b", r["question"], re.I) and not r["question"].startswith("/") and not CORRECTION.search(r["question"]):
            why.append("no tool used")
        if why:
            st["failures"].append({"q": r["question"], "why": "; ".join(why), "tools": [e["name"] for e in tool_events], "answer": r.get("answer", "")[:160]})
        if r.get("seconds", 0) > 60:
            st["slow"].append((r["question"][:60], round(r["seconds"])))
        p = prev_by_channel.get(r["channel"])
        if p and CORRECTION.search(r["question"]):
            st["corrections"].append({"q": p["question"], "a": p.get("answer", "")[:160], "said": r["question"][:160]})
        prev_by_channel[r["channel"]] = r
    return st


# ------------------------------------------------------------------ the whole job
async def run(svc, send):
    """svc gives the registry, the lock and memory. `send` posts to Telegram (or None)."""
    day = local_day()
    os.makedirs(SNAP_DIR, exist_ok=True)
    os.makedirs(DIGEST_DIR, exist_ok=True)
    st = analyse(read_day(day))
    async with svc.lock:                      # never run tools while a user request is being answered
        cur = await snapshot(svc.reg)
    prevs = sorted(f for f in os.listdir(SNAP_DIR) if f.endswith(".json") and f[:-5] < day)
    prev = json.load(open(f"{SNAP_DIR}/{prevs[-1]}")) if prevs else None
    changes = diff(prev, cur) if prev else []
    json.dump(cur, open(f"{SNAP_DIR}/{day}.json", "w"), indent=1)

    proposals = []
    mem = svc.agent.memory
    for c in st["corrections"][:3]:
        t = f"The owner said an answer was wrong. Question: {c['q'][:70]} / My answer: {c['a'][:70]} / Owner: {c['said'][:70]}"
        if (mid := mem.add(t, source="nightly", kind="correction")):
            proposals.append(mid)
    for line in changes:
        if line.startswith(("New Proxmox guest", "New Docker container", "New device seen")):
            if (mid := mem.add(f"{line} (first seen {day})", source="nightly", kind="discovery")):
                proposals.append(mid)
    if st["failures"]:
        with open(CANDIDATES, "a") as f:
            for fl in st["failures"]:
                f.write(json.dumps({"day": day, **fl}, ensure_ascii=False) + "\n")
    purge_old()

    L = [f"Daily review {day}"]
    L.append(f"Requests: {st['n']} ({', '.join(f'{k} {v}' for k, v in st['channels'].items()) or 'none'}); average {st['avg_s']} s, slowest {st['max_s']} s.")
    if st["top_tools"]:
        L.append("Most used tools: " + ", ".join(f"{n} x{c}" for n, c in st["top_tools"]))
    L.append("")
    if prev:
        L.append(f"Lab changes since {prevs[-1][:-5]}:" if changes else f"Lab changes since {prevs[-1][:-5]}: none.")
        L += [f"- {c}" for c in changes[:10]]
    else:
        L.append("First snapshot of the lab saved; changes will show from tomorrow.")
    if getattr(svc, "alerts", None):
        import datetime as _dt
        L.append(svc.alerts.summary_line(_dt.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()))
    L.append("Disabled or snoozed by me, undone automatically: " + svc.agent.timers.describe())
    pr = cur.get("problems")
    if pr:
        L.append(f"Zabbix: {pr['total']} active problems ({', '.join(f'{k} {v}' for k, v in pr['by_severity'].items())}).")
    L.append("")
    if st["failures"]:
        L.append(f"Answers to review ({len(st['failures'])}):")
        L += [f"- \"{f['q'][:70]}\" -> {f['why']}" for f in st["failures"][:6]]
        L.append("Saved to the eval candidates file for turning into checked test cases.")
    else:
        L.append("No failed or doubtful answers today.")
    if st["corrections"]:
        L.append(f"You corrected me {len(st['corrections'])} time(s); proposals below.")
    if st["slow"]:
        L.append("Slow (over 60 s): " + "; ".join(f"\"{q}\" {s}s" for q, s in st["slow"][:3]))
    if st["repeated"]:
        L.append("Asked more than once: " + "; ".join(f"\"{q[:50]}\" x{n}" for q, n in st["repeated"][:3]))
    if proposals:
        L.append("")
        L.append("Memory proposals (nothing changes until you approve):")
        for mid in proposals[:6]:
            L.append(f"  #{mid} {mem.db.execute('select text from memory where id=?', (mid,)).fetchone()[0][:110]}")
        L.append("Reply /approve N or /reject N. /memory lists everything.")
    text = "\n".join(L)
    open(f"{DIGEST_DIR}/{day}.txt", "w").write(text)
    if send:
        await send(text)
    return text


def latest_digest():
    try:
        files = sorted(os.listdir(DIGEST_DIR))
        return open(f"{DIGEST_DIR}/{files[-1]}").read() if files else "No daily review has run yet."
    except OSError:
        return "No daily review has run yet."


def due(state_file=STATE_FILE, hour=REVIEW_HOUR):
    """True once per local day, from `hour` on. Survives restarts (a missed run happens at the next start the same day)."""
    now = datetime.datetime.now(TZ)
    try:
        last = open(state_file).read().strip()
    except OSError:
        last = ""
    return now.hour >= hour and last != local_day()


def mark_done(state_file=STATE_FILE):
    open(state_file, "w").write(local_day())

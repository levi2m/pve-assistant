"""Offline tests for the alert engine: fake clock, fake Zabbix MCP, fake Telegram. python3 alerts_test.py"""
import asyncio, datetime, json, os, sqlite3, sys, types
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from pve_assistant import alerts as A
import pve_assistant.loop as L

TZ = A.TZ                      # the configured timezone (UTC when no config file exists)
ok_count = bad_count = 0
def check(name, cond, extra=""):
    global ok_count, bad_count
    ok_count += cond; bad_count += (not cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if (extra and not cond) else ""))

def at(h, m=0, day=21):   # a local timestamp
    return datetime.datetime(2026, 10, day, h, m, tzinfo=TZ).timestamp()

def prob(eid, sev, name="Linux: Load average is too high", host="webhost", clock=None, ack="0"):
    return {"eventid": str(eid), "triggerid": "1", "host": host, "name": name, "severity": str(sev), "severity_label": {3: "average", 4: "high", 5: "disaster", 2: "warning", 1: "information"}[sev],
            "clock": str(int(clock if clock is not None else at(12))), "acknowledged": ack}

class FakeReg:
    def __init__(self): self.problems, self.still, self.down = [], set(), False
    async def call_raw(self, tool, args):
        if self.down and tool == "problem_active_get": raise RuntimeError("zabbix down")
        if tool == "problem_active_get": return False, json.dumps({"problems": self.problems, "count": len(self.problems)})
        if tool == "problem_get": return False, json.dumps([{"eventid": e, "r_eventid": "0"} for e in args["eventids"] if e in self.still])
        return False, "[]"

class FakeTG:
    def __init__(self): self.sent, self.cleared, self.n = [], [], 100
    async def send(self, text, buttons=None, reply_to=None):
        self.n += 1; self.sent.append({"text": text, "buttons": buttons, "reply_to": reply_to, "id": self.n}); return self.n
    async def clear_buttons(self, mid): self.cleared.append(mid)

def make():
    db = sqlite3.connect(":memory:")
    svc = types.SimpleNamespace(agent=types.SimpleNamespace(audit=types.SimpleNamespace(db=db)), reg=FakeReg(), lock=asyncio.Lock(), log=print)
    tg = FakeTG()
    al = A.Alerter(svc, tg)
    return al, svc, tg

async def suggestion_stub(*a, **k): return None
L.chat = lambda *a, **k: ({"content": "Check the load on webhost first."}, 1, 1)

async def main():
    global ok_count, bad_count
    ok_count = bad_count = 0
    cfg = A.Cfg()
    # ---- decide()
    d = lambda sev, age_min, hour, **kw: A.decide(prob(1, sev, clock=at(12) - 0), at(12) + age_min * 60, hour, cfg, **kw)
    check("decide: high is sent at once, even at 03:00", d(4, 0, 3) == "send")
    check("decide: disaster at once", d(5, 0, 12) == "send")
    check("decide: average younger than 15 min waits", d(3, 5, 12) == "wait")
    check("decide: average older than 15 min is sent", d(3, 16, 12) == "send")
    check("decide: average during quiet hours waits", d(3, 40, 2) == "wait" and d(3, 40, 23) == "wait" and d(3, 40, 6) == "wait")
    check("decide: average is sent from 07:00", d(3, 40, 7) == "send")
    check("decide: muted average waits, muted high still sent", d(3, 40, 12, muted=True) == "wait" and d(4, 0, 12, muted=True) == "send")
    check("decide: warning and information are ignored", d(2, 90, 12) == "ignore" and d(1, 90, 12) == "ignore")
    check("decide: acknowledged problem is ignored", A.decide(prob(1, 5, ack="1"), at(12), 12, cfg) == "ignore")
    check("remind_due: high unacked after 4 h, max 3", A.remind_due((4, "sent", at(8), None, 0), at(12, 1), cfg) and not A.remind_due((4, "sent", at(8), None, 3), at(20), cfg) and not A.remind_due((3, "sent", at(8), None, 0), at(20), cfg))
    check("core_name strips host-specific parts", A.core_name("Proxmox VE: Node [nodehost]: LXC [template-guest]: Not running") == "Proxmox VE: Node : LXC : Not running" and A.core_name("Linux: Load average is too high (per CPU load over 1.5 for 5m)") == "Linux: Load average is too high")

    # ---- suggestion validator
    check("suggestion: plain advice kept", A.valid_suggestion("Check what is using CPU on webhost.", "", "load = 3.2") == "Check what is using CPU on webhost.")
    check("suggestion: invented number dropped", A.valid_suggestion("Restart after 45 minutes.", "", "load = 3.2") is None)
    check("suggestion: number from the evidence kept", A.valid_suggestion("Load is 3.2, check processes.", "", "load = 3.2") == "Load is 3.2, check processes.")
    check("suggestion: command dropped, command quoted from the owner's note kept", A.valid_suggestion("Run sudo reboot.", "", "") is None and A.valid_suggestion("Use sudo systemctl restart nginx as noted.", "sudo systemctl restart nginx", "") is not None)
    check("suggestion: too long / url dropped", A.valid_suggestion("x" * 300) is None and A.valid_suggestion("see https://x.io") is None)

    f_err = {"count": 0, "notes": [], "error": "boom"}
    txt = A.compose(prob(1, 4), 120, f_err, [], None)
    check("compose: a failed fixes lookup is stated, not reported as 'no fix noted'", "could not read" in txt.lower() and "No fix noted" not in txt, txt)

    # ---- cycle: baseline, send, no repeat
    al, svc, tg = make()
    svc.reg.problems = [prob(1, 3, clock=at(10)), prob(2, 2, clock=at(10)), prob(3, 1, clock=at(10))]
    r = await al.cycle(at(12)); check("baseline: nothing sent on the first run, existing sub-high problems recorded", not tg.sent and r["baseline"] == 3, str(r))
    r = await al.cycle(at(12, 1)); check("baseline problems stay silent afterwards", not tg.sent)
    svc.reg.problems.append(prob(4, 4, "Proxmox VE: Node [nodehost] is down", "nodehost", clock=at(12, 1)))
    r = await al.cycle(at(12, 2)); check("a new HIGH problem is announced with Acknowledge and Details buttons", len(tg.sent) == 1 and tg.sent[0]["buttons"][0][0][1] == "ack:4" and tg.sent[0]["buttons"][0][1][1] == "why:4", str(tg.sent))
    check("alert text carries severity, host, name, event and a suggestion", all(s in tg.sent[0]["text"] for s in ("HIGH", "nodehost", "is down", "event 4", "Suggestion: Check the load on webhost first.", "First time I see this")), tg.sent[0]["text"])
    r = await al.cycle(at(12, 3)); check("the same problem is not announced twice", len(tg.sent) == 1)

    # ---- persistence and quiet hours
    al, svc, tg = make(); svc.reg.problems = []
    await al.cycle(at(12))                                       # baseline done (empty)
    svc.reg.problems = [prob(10, 3, clock=at(12, 0))]
    await al.cycle(at(12, 5)); check("average: not announced after 5 minutes", not tg.sent)
    await al.cycle(at(12, 16)); check("average: announced once it has been open 15 minutes", len(tg.sent) == 1 and "AVERAGE" in tg.sent[0]["text"])
    await al.cycle(at(12, 20)); check("average: not repeated", len(tg.sent) == 1)
    al, svc, tg = make(); await al.cycle(at(22))
    svc.reg.problems = [prob(11, 3, clock=at(22, 40))]
    await al.cycle(at(23, 30)); await al.cycle(at(3, 0, day=22)); check("average found at night: silent during quiet hours", not tg.sent)
    await al.cycle(at(7, 5, day=22)); check("average still open at 07:05: announced in the morning", len(tg.sent) == 1)
    al, svc, tg = make(); await al.cycle(at(22)); svc.reg.problems = [prob(12, 4, clock=at(2, 0, day=22))]
    await al.cycle(at(2, 1, day=22)); check("high at 02:00 is announced despite quiet hours", len(tg.sent) == 1)
    al, svc, tg = make(); await al.cycle(at(12)); al.store.set_meta("muted_until", at(14)); svc.reg.problems = [prob(13, 3, clock=at(12)), prob(14, 5, "Disk full", "srvhost", clock=at(12))]
    await al.cycle(at(12, 30)); check("mute: average held back, disaster still announced", len(tg.sent) == 1 and "DISASTER" in tg.sent[0]["text"])
    al, svc, tg = make(); await al.cycle(at(12)); svc.reg.problems = [prob(15, 5, ack="1")]
    await al.cycle(at(12, 1)); check("a problem someone already acknowledged is not announced", not tg.sent)

    # ---- flapping, burst, hourly cap
    al, svc, tg = make(); await al.cycle(at(12)); t = at(12, 1)
    for i in range(1, 6):
        svc.reg.problems = [prob(100 + i, 4, "Linux: High memory utilization", "dbhost", clock=t)]
        await al.cycle(t); t += 600
    texts = [s["text"] for s in tg.sent]
    check("flapping: 2 normal alerts, then ONE flapping notice, then silence (no 3rd/4th alert)", sum("HIGH" in x for x in texts) == 2 and sum("Flapping" in x for x in texts) == 1, str([x[:22] for x in texts]))
    al, svc, tg = make(); await al.cycle(at(12)); svc.reg.problems = [prob(200 + i, 4, f"Problem number {i}", f"host{i}", clock=at(12, 1)) for i in range(5)]
    await al.cycle(at(12, 2)); check("burst: 5 new problems become ONE grouped message", len(tg.sent) == 1 and "5 problems" in tg.sent[0]["text"] and tg.sent[0]["buttons"] is None, str(len(tg.sent)))
    al, svc, tg = make(); await al.cycle(at(12)); t = at(12, 1)
    for i in range(10):
        svc.reg.problems = [prob(300 + i, 4, f"Distinct problem {i}", f"h{i}", clock=t)]
        await al.cycle(t); t += 60
    check("hourly cap: no more than 8 individual messages an hour (rest grouped)", sum(1 for s in tg.sent if s["buttons"]) <= 8 and len(tg.sent) >= 9, str(len(tg.sent)))

    # ---- reminders and resolution
    al, svc, tg = make(); await al.cycle(at(8)); svc.reg.problems = [prob(400, 4, clock=at(8, 0))]
    await al.cycle(at(8, 1)); n0 = len(tg.sent)
    await al.cycle(at(10)); check("no reminder after 2 hours", len(tg.sent) == n0)
    await al.cycle(at(12, 2)); check("reminder after 4 hours, as a reply to the alert", len(tg.sent) == n0 + 1 and tg.sent[-1]["reply_to"] == tg.sent[0]["id"] and "Still open" in tg.sent[-1]["text"])
    await al.cycle(at(16, 3)); await al.cycle(at(20, 4)); await al.cycle(at(23, 59)); await al.cycle(at(3, 5, day=22))
    check("at most 3 reminders", sum("Still open" in s["text"] for s in tg.sent) == 3, str([s["text"][:20] for s in tg.sent]))
    svc.reg.problems = []; svc.reg.still = set()
    await al.cycle(at(4, 0, day=22)); check("resolved: message sent as a reply and buttons removed", "Resolved" in tg.sent[-1]["text"] and tg.cleared == [tg.sent[0]["id"]])
    await al.cycle(at(4, 1, day=22)); check("resolved is announced once", sum("Resolved" in s["text"] for s in tg.sent) == 1)
    al, svc, tg = make(); await al.cycle(at(12)); svc.reg.problems = [prob(500, 4, clock=at(12))]; await al.cycle(at(12, 1))
    svc.reg.problems = []; svc.reg.still = {"500"}; await al.cycle(at(12, 5)); check("problem hidden from the active view but still open is NOT reported resolved", not any("Resolved" in s["text"] for s in tg.sent))

    al, svc, tg = make(); await al.cycle(at(12)); svc.reg.problems = [prob(700, 4, clock=at(12))]; await al.cycle(at(12, 1))
    al.store.update("700", state="acked"); await al.cycle(at(12, 30)); check("acknowledged alert: no reminder", not any("Still open" in s["text"] for s in tg.sent))
    svc.reg.problems = []; await al.cycle(at(13)); check("acknowledged alert still gets a resolved notice", any("Resolved" in s["text"] for s in tg.sent))

    # ---- zabbix outage
    al, svc, tg = make(); await al.cycle(at(12)); svc.reg.down = True
    for i in range(7): await al.cycle(at(12, 1 + i))
    check("zabbix unreachable: exactly one message after 5 failed checks", sum("cannot reach Zabbix" in s["text"] for s in tg.sent) == 1)
    svc.reg.down = False; await al.cycle(at(12, 20)); check("zabbix back: one recovery message", sum("reachable again" in s["text"] for s in tg.sent) == 1)

    # ---- restart safety
    al, svc, tg = make(); await al.cycle(at(12)); svc.reg.problems = [prob(600, 4, clock=at(12))]; await al.cycle(at(12, 1))
    al2 = A.Alerter(svc, tg); await al2.cycle(at(12, 2)); check("a restart does not repeat an alert already sent", len(tg.sent) == 1)
    print(f"\n{ok_count}/{ok_count + bad_count} alert tests passed")

def test_alert_engine():
    asyncio.run(main())
    assert bad_count == 0, f"{bad_count} alert checks failed"


if __name__ == "__main__":
    test_alert_engine()

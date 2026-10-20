"""The long-running agent: one model, one request at a time, reachable by Telegram and an authenticated HTTP API."""
import asyncio
import contextlib
import hmac
import json
import re
import os
import signal
import sys
import time
from collections import deque

import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import config, nightly
from .config import REVIEW_HOUR, STATE_DIR, TIMEZONE, load_env
from .alerts import Alerter, ICON
from .loop import Agent, chat
from .memory import extract_remember
from .registry import Registry
from .telegram import Telegram

MEMORY_TTL = 1800        # follow-ups remember the last turns for 30 minutes
MAX_WAITING = 3          # requests queued behind the one being answered
MAX_QUESTION = 1000


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


class Service:
    def __init__(self, reg):
        self.reg, self.agent = reg, Agent(reg)
        self.agent.dry_run = os.environ.get("ASSISTANT_DRYRUN") == "1"
        self.lock, self.waiting = asyncio.Lock(), 0
        self.memory: dict[str, list] = {}
        self.started, self.infra_errors, self.served = time.time(), 0, 0
        self.stopping = asyncio.Event()
        self.log, self.tg, self.alerts = log, None, None

    # ---------------------------------------------------------------- one question in, one answer out
    def remember_reply(self, text):
        """'... add to your memory that X' is done by code, so it is really saved; the model cannot write memory and used to claim it had."""
        note = extract_remember(text)
        if not note:
            return None
        try:
            mid = self.agent.memory.add(note, source="owner")
        except ValueError as e:
            return f"I did not save that: {e}."
        rest = " I did not act on the rest of your message: I cannot undo changes by myself, tell me exactly what you want changed." if re.search(r"\bundo\b|\brevert\b|\broll ?back\b", text, re.I) else ""
        return (f"Saved to my memory as #{mid}: {note}." if mid else "I already have that in my memory.") + rest

    async def handle(self, channel, who, text, remember=True):
        if (reply := self.remember_reply(text)):
            return reply
        if self.waiting >= MAX_WAITING:
            return "I am busy with other requests. Try again in a minute."
        self.waiting += 1
        try:
            async with self.lock:
                hist = [h for h in self.memory.get(who, []) if time.time() - h[2] < MEMORY_TTL] if remember else []
                t0 = time.time()
                try:
                    answer = await self.agent.ask(text, channel=channel, trace=lambda m: None, history=[(q, a) for q, a, _ in hist])
                    self.infra_errors = 0
                except Exception as e:  # noqa: BLE001
                    self.infra_errors += 1
                    log(f"ask failed ({self.infra_errors}): {type(e).__name__}: {str(e)[:200]}")
                    if self.infra_errors >= 3:          # tools or model are wedged: let systemd restart a clean process
                        log("three failures in a row: exiting for a restart")
                        asyncio.get_running_loop().call_later(2, os._exit, 1)
                    return "I could not answer that: a tool or the model failed. It is logged."
                dead = [name for name, n in self.reg.failures.items() if n >= 3]
                if dead:                               # a tool server's process died: restart everything cleanly
                    log(f"MCP server(s) not responding: {dead}; exiting for a restart")
                    asyncio.get_running_loop().call_later(2, os._exit, 1)
                self.served += 1
                log(f"{channel} answered in {time.time() - t0:.0f}s via {self.agent.last_tools or 'no tools'}")
                if remember:
                    self.memory[who] = (hist + [(text, answer, time.time())])[-3:]
                return answer
        finally:
            self.waiting -= 1

    # ---------------------------------------------------------------- commands (Telegram)
    def status_text(self):
        up = int(time.time() - self.started)
        db = self.agent.audit.db
        n, = db.execute("select count(*) from requests where ts > ?", (time.time() - 3600,)).fetchone()
        errs = f"; unavailable: {', '.join(self.reg.errors)}" if self.reg.errors else ""
        return (f"Assistant up {up // 3600}h{up % 3600 // 60:02d}m, model {config.MODEL}\n"
                f"MCP servers: {len(self.reg.sessions)} connected{errs}, {len(self.reg.tools)} tools\n"
                f"Answered {self.served} since start, {n} in the last hour. Queue: {self.waiting}\n"
                f"Memory: {len(self.agent.memory.list('approved'))} notes, {len(self.agent.memory.list('proposed'))} waiting for approval. Daily review at {REVIEW_HOUR}:00.\n"
                f"Undo timers: {self.agent.timers.describe()}"
                + ("\nDRY-RUN: changes are not executed" if self.agent.dry_run else ""))

    async def telegram_handler(self, text):
        parts = text.split(None, 1)
        cmd = parts[0].lower().split("@")[0] if text.startswith("/") else ""
        arg = parts[1].strip() if len(parts) > 1 else ""
        mem = self.agent.memory
        if cmd in ("/start", "/help"):
            return ("I answer questions about the lab and can make one change per request.\n"
                    "/status  health of the assistant\n/clear  forget this conversation\n"
                    "/remember <fact>  teach me a fact about the lab\n/memory  what I remember and what waits for approval\n"
                    "/approve N, /reject N, /forget N  manage memory\n/digest  last daily review\n/review  run the daily review now\n/alerts  open alerts   /fixes <text>  past fixes   /mute <hours>, /unmute")
        if cmd == "/status":
            return self.status_text()
        if cmd == "/clear":
            self.memory.pop("telegram", None)
            return "Conversation cleared."
        if cmd == "/remember":
            try:
                mid = mem.add(arg, source="owner")
            except ValueError as e:
                return f"Not saved: {e}."
            return f"Remembered as #{mid}." if mid else "I already know that."
        if cmd == "/memory":
            rows = mem.list()
            live = [r for r in rows if r[4] in ("approved", "proposed")]
            if not live:
                return "My memory is empty. Teach me with /remember <fact>."
            return "\n".join(f"#{i} [{st}] {t[:140]}" for i, _k, t, _s, st in live[-20:])
        if cmd in ("/approve", "/reject", "/forget"):
            if not arg.lstrip("#").isdigit():
                return f"Usage: {cmd} <number>"
            status = "approved" if cmd == "/approve" else "rejected"
            return f"Note #{arg.lstrip('#')} {status}." if mem.set_status(int(arg.lstrip("#")), status) else "No such note."
        if cmd in ("/alerts", "/fixes", "/mute", "/unmute") and self.alerts is None:
            return "Alerts are off: they need the Telegram bridge (no telegram.env). Zabbix keeps alerting through its own media types."
        if cmd == "/alerts":
            st = self.alerts.store
            rows = st.open_sent()
            muted = float(st.meta("muted_until", 0)) > time.time()
            head = f"Alerts: {len(rows)} open and announced" + ("; average-level alerts are MUTED" if muted else "") + f". Rules: high+ at once, average after {self.alerts.cfg.persist_min} min, quiet {self.alerts.cfg.quiet_start}:00-{self.alerts.cfg.quiet_end}:00."
            return "\n".join([head] + [f"- {ICON.get(sev, '').split(' ')[0]} {host}: {name[:70]} (event {eid})" for eid, host, name, sev, _m, _t in rows[:10]])
        if cmd == "/fixes":
            if len(arg) < 4:
                return "Usage: /fixes <part of a problem name>, for example /fixes load average"
            f = await self.alerts.known_fixes(arg)
            if not f["notes"]:
                return f"No fix comments found in Zabbix for '{arg}' (seen {f['count']}x in the last 7 days). Add a comment when you solve one and I will find it here."
            return f"'{arg}': seen {f['count']}x in the last 7 days. Fixes noted:\n" + "\n".join(f"- {n['date']} {n['host']}: {n['text'][:200]}" for n in f["notes"][:5])
        if cmd == "/mute":
            hours = float(arg) if arg.replace(".", "", 1).isdigit() else 0
            if not 0 < hours <= 48:
                return "Usage: /mute <hours 1-48>. Average-level alerts pause; high and disaster still reach you."
            self.alerts.store.set_meta("muted_until", time.time() + hours * 3600)
            return f"Average-level alerts muted for {hours:g} h. High and disaster still come through. /unmute to undo."
        if cmd == "/unmute":
            self.alerts.store.set_meta("muted_until", 0)
            return "Alerts unmuted."
        if cmd == "/digest":
            return nightly.latest_digest()
        if cmd == "/review":
            return await nightly.run(self, None)
        if cmd:
            return "Unknown command. Try /help."
        return await self.handle("telegram", "telegram", text)

    async def alert_callback(self, data, mid):
        """Inline buttons on an alert: Acknowledge / Details."""
        kind, _, eid = data.partition(":")
        if not eid.isdigit():
            return "Unknown button."
        if kind == "ack":
            self.reg.current_rid = None
            is_err, text = await self.reg.call("zabbix_ack_problem", {"problem": eid, "message": "acknowledged from Telegram alert"})
            if not is_err:
                await self.tg.clear_buttons(mid)
                await self.tg.send("✔ " + text, reply_to=mid)
                if self.alerts.store.get(eid):
                    self.alerts.store.update(eid, state="acked")
            return text[:150]
        if kind == "why":
            asyncio.create_task(self._why(eid, mid))
            return "Looking into it..."
        return "Unknown button."

    async def _why(self, eid, mid):
        answer = await self.handle("telegram", "alert", f"Investigate Zabbix problem with event id {eid}. What is wrong, what is the likely cause, and what should I check first?", remember=False)
        await self.tg.send(answer, reply_to=mid)

    async def timer_loop(self, send):
        """Undo what the agent did for a limited time (re-enable hosts, unsnooze problems). A failure is retried hourly and reported."""
        from . import recipes
        while True:
            await asyncio.sleep(60)
            for tid, kind, lab, args, _rid, attempts in self.agent.timers.due():
                try:
                    msg = await recipes.run_timer(self.reg, kind, json.loads(args))
                    self.agent.timers.done(tid)
                    log(f"timer {tid}: {msg}")
                    await send(msg)
                except Exception as e:  # noqa: BLE001
                    self.agent.timers.failed(tid, str(e))
                    log(f"timer {tid} failed ({attempts + 1}): {str(e)[:150]}")
                    if attempts in (0, 5):
                        try:
                            await send(f"I could not undo '{lab}' automatically ({str(e)[:120]}). I will retry in an hour.")
                        except Exception:  # noqa: BLE001
                            pass

    async def nightly_loop(self, send):
        """Run the daily review once per local day from the configured hour; a failure is retried an hour later."""
        while True:
            await asyncio.sleep(60)
            if nightly.due():
                try:
                    await nightly.run(self, send)
                    nightly.mark_done()
                    log("daily review sent")
                except Exception as e:  # noqa: BLE001
                    log(f"daily review failed: {type(e).__name__}: {str(e)[:200]}")
                    await asyncio.sleep(3600)

    # ---------------------------------------------------------------- HTTP API
    def api(self, key, bind, port):
        hits = deque()

        def authed(request):
            return bool(key) and hmac.compare_digest(request.headers.get("x-api-key", ""), key)

        async def ask(request):
            if not authed(request):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            now = time.time()
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= 10:
                return JSONResponse({"error": "rate limit: 10 requests per minute"}, status_code=429)
            hits.append(now)
            try:
                body = json.loads((await request.body())[:4096] or b"{}")
                q = str(body["question"]).strip()
            except Exception:  # noqa: BLE001
                return JSONResponse({"error": 'send JSON like {"question": "..."}'}, status_code=400)
            if not q or len(q) > MAX_QUESTION:
                return JSONResponse({"error": f"question must be 1-{MAX_QUESTION} characters"}, status_code=400)
            session = "".join(ch for ch in str(body.get("session", "")) if ch.isalnum() or ch in "-_")[:40]
            t0 = time.time()
            if q.startswith("/"):                 # same commands as Telegram (/memory, /remember, /review...) for the key holder
                answer = await self.telegram_handler(q)
                return JSONResponse({"answer": answer, "tools": [], "seconds": round(time.time() - t0, 1)})
            answer = await self.handle("api", f"api:{session}", q, remember=bool(session))
            return JSONResponse({"answer": answer, "tools": self.agent.last_tools, "seconds": round(time.time() - t0, 1)})

        async def status(request):
            if not authed(request):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return JSONResponse({"status": self.status_text(), "servers": sorted(self.reg.sessions), "errors": self.reg.errors})

        async def health(request):          # no auth, no details: for Zabbix or a load balancer
            ok = len(self.reg.sessions) >= 4 and not any(n >= 2 for n in self.reg.failures.values())
            return JSONResponse({"ok": ok, "uptime_s": int(time.time() - self.started)}, status_code=200 if ok else 503)

        app = Starlette(routes=[Route("/ask", ask, methods=["POST"]), Route("/status", status), Route("/health", health)])
        return uvicorn.Server(uvicorn.Config(app, host=bind, port=port, log_level="warning", access_log=False))


async def serve():
    svc_env = load_env("service.env")
    tg_env = load_env("telegram.env")
    log("connecting to MCP servers...")
    reg = await Registry().start()
    log(f"connected: {sorted(reg.sessions)}; errors: {reg.errors or 'none'}; tools: {len(reg.tools)}")
    svc = Service(reg)
    await svc.agent.refresh_protected()
    asyncio.get_running_loop().run_in_executor(None, lambda: chat([{"role": "user", "content": "ok"}], None, num_ctx=512, num_predict=1))   # warm the model
    tasks = []
    key = svc_env.get("ASSISTANT_API_KEY", "")
    if key:
        server = svc.api(key, svc_env.get("ASSISTANT_API_BIND", "127.0.0.1"), int(svc_env.get("ASSISTANT_API_PORT", "8099")))
        server.install_signal_handlers = lambda: None                      # older uvicorn
        server.capture_signals = lambda: contextlib.nullcontext()          # newer uvicorn: keep our own SIGTERM handling
        tasks.append(asyncio.create_task(server.serve()))
        log(f"HTTP API on {svc_env.get('ASSISTANT_API_BIND', '127.0.0.1')}:{svc_env.get('ASSISTANT_API_PORT', '8099')}")
    else:
        log("no ASSISTANT_API_KEY: HTTP API disabled")
    if tg_env.get("TELEGRAM_BOT_TOKEN") and tg_env.get("TELEGRAM_CHAT_ID"):
        tg = Telegram(tg_env["TELEGRAM_BOT_TOKEN"], tg_env["TELEGRAM_CHAT_ID"], svc.telegram_handler, log, svc.alert_callback)
        svc.tg = tg
        svc.alerts = Alerter(svc, tg)
        tasks.append(asyncio.create_task(tg.run()))
        tasks.append(asyncio.create_task(svc.nightly_loop(tg.send)))
        tasks.append(asyncio.create_task(svc.timer_loop(tg.send)))
        tasks.append(asyncio.create_task(svc.alerts.loop()))
        log(f"alerts: high+ at once, average after {svc.alerts.cfg.persist_min} min, quiet {svc.alerts.cfg.quiet_start}:00-{svc.alerts.cfg.quiet_end}:00, polling every {svc.alerts.cfg.poll_s} s")
        log(f"Telegram bridge polling; daily review scheduled for {REVIEW_HOUR}:00 {TIMEZONE}")
        if os.path.exists(os.path.join(STATE_DIR, "notify-once")):
            os.remove(os.path.join(STATE_DIR, "notify-once"))
            try:
                await tg.send("Online. Ask me about the lab, or send /help.")
            except Exception as e:  # noqa: BLE001
                log(f"startup message failed: {type(e).__name__}")
    else:
        # Offline mode: no Telegram, so no proactive alerts, but the safety jobs still run (undo timers must fire) and the review is kept on disk.
        async def keep_local(text):
            log(f"(local) {text[:160]}")
        tasks.append(asyncio.create_task(svc.nightly_loop(keep_local)))
        tasks.append(asyncio.create_task(svc.timer_loop(keep_local)))
        log("no Telegram settings: bridge and alerts disabled; undo timers and the daily review still run (read the review with /digest)")
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, svc.stopping.set)
    await svc.stopping.wait()
    log("stopping")
    for t in tasks:
        t.cancel()
    try:
        await asyncio.wait_for(reg.close(), 8)
    except BaseException:  # noqa: BLE001
        pass
    os._exit(0)

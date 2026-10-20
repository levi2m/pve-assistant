"""Full transcripts: one JSON line per request with everything the agent saw, said and did, secrets redacted.

The model runs with thinking off, so there is no hidden reasoning to log. What IS logged is every visible step: the gate's
choices, each model turn (its text and tool calls), each tool call with arguments and a trimmed result, and the final answer."""
import datetime
import json
import os
import re
import time

from .config import STATE_DIR, TZ

LOG_DIR = os.path.join(STATE_DIR, "log")
KEEP_DAYS = 90
RESULT_KEEP = 1500
_SECRETS = [re.compile(p) for p in (
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",                       # JWTs
    r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b",                                                      # Telegram bot tokens
    r"\b[A-Fa-f0-9]{32,}\b",                                                                  # long hex keys
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",       # UUIDs (Proxmox token secrets)
    r"(?i)(password|passwd|secret|token|api[_-]?key|authorization)(\\?\"?\s*[:=]\s*\\?\"?)[^\s\\\",}]{6,}",   # key=value / "key": "value"
)]


def redact(text):
    for rx in _SECRETS:
        text = rx.sub(lambda m: (m.group(1) + m.group(2) + "[redacted]") if rx.groups >= 2 else "[redacted]", text)
    return text


def local_day(ts=None):
    return datetime.datetime.fromtimestamp(ts or time.time(), TZ).strftime("%Y-%m-%d")


class Transcript:
    def __init__(self, channel, question):
        self.t0 = time.time()
        self.d = {"ts": self.t0, "channel": channel, "question": question, "events": []}

    def event(self, kind, **kw):
        self.d["events"].append({"t": round(time.time() - self.t0, 2), "kind": kind, **kw})

    def tool_result(self, text):
        return text if len(text) <= RESULT_KEEP else text[:RESULT_KEEP] + f"...[+{len(text) - RESULT_KEEP} chars]"

    def save(self, answer, outcome):
        self.d.update(answer=answer, outcome=outcome, seconds=round(time.time() - self.t0, 1))
        try:
            os.makedirs(LOG_DIR, mode=0o700, exist_ok=True)
            line = redact(json.dumps(self.d, ensure_ascii=False, default=str))
            fd = os.open(f"{LOG_DIR}/{local_day(self.t0)}.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a") as f:
                f.write(line + "\n")
        except OSError:
            pass          # logging must never break an answer


def purge_old():
    cutoff = time.time() - KEEP_DAYS * 86400
    try:
        for name in os.listdir(LOG_DIR):
            p = os.path.join(LOG_DIR, name)
            if name.endswith(".jsonl") and os.path.getmtime(p) < cutoff:
                os.remove(p)
    except OSError:
        pass


def read_day(day):
    try:
        return [json.loads(l) for l in open(f"{LOG_DIR}/{day}.jsonl") if l.strip()]
    except OSError:
        return []

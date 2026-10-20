"""Persistent timers for 'undo later': re-enable what the agent disabled, unsuppress what it snoozed.
Stored in the audit database so a restart never forgets one. A failed undo is retried every hour and reported."""
import json
import time

RETRY_S = 3600


class Timers:
    def __init__(self, db):
        self.db = db
        db.execute("""CREATE TABLE IF NOT EXISTS timers(id INTEGER PRIMARY KEY, ts REAL, due REAL, kind TEXT, label TEXT, args TEXT,
                      request_id INTEGER, status TEXT, attempts INTEGER DEFAULT 0, last_error TEXT)""")
        db.commit()

    def add(self, kind, label, args, hours, request_id=None):
        # one open timer per target: a new disable/snooze replaces the old one
        self.db.execute("UPDATE timers SET status='replaced' WHERE status='open' AND kind=? AND label=?", (kind, label))
        cur = self.db.execute("INSERT INTO timers(ts,due,kind,label,args,request_id,status) VALUES(?,?,?,?,?,?,'open')",
                              (time.time(), time.time() + hours * 3600, kind, label, json.dumps(args), request_id))
        self.db.commit()
        return cur.lastrowid

    def cancel(self, kind, label):
        cur = self.db.execute("UPDATE timers SET status='cancelled' WHERE status='open' AND kind=? AND label=?", (kind, label))
        self.db.commit()
        return cur.rowcount

    def due(self, now=None):
        return self.db.execute("select id,kind,label,args,request_id,attempts from timers where status='open' and due<=? order by due", (now or time.time(),)).fetchall()

    def done(self, tid):
        self.db.execute("UPDATE timers SET status='done' WHERE id=?", (tid,))
        self.db.commit()

    def failed(self, tid, error):
        self.db.execute("UPDATE timers SET attempts=attempts+1, last_error=?, due=? WHERE id=?", (error[:200], time.time() + RETRY_S, tid))
        self.db.commit()

    def open(self):
        return self.db.execute("select id,kind,label,due,attempts from timers where status='open' order by due").fetchall()

    def describe(self):
        rows = self.open()
        if not rows:
            return "none"
        out = []
        for _id, kind, label, due, attempts in rows:
            mins = max(0, int((due - time.time()) / 60))
            out.append(f"{label} ({'re-enable' if kind.startswith('enable') else 'unsnooze'} in {mins // 60}h{mins % 60:02d}m" + (f", {attempts} failed attempt(s)" if attempts else "") + ")")
        return "; ".join(out)

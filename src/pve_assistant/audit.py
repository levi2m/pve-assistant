"""SQLite audit trail: every request and every tool call, with arguments (never credentials) and outcome."""
import json
import sqlite3
import time

from .config import DB


class Audit:
    def __init__(self, path=DB):
        self.db = sqlite3.connect(path)
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY, ts REAL, channel TEXT, question TEXT, answer TEXT, steps INTEGER, ms INTEGER, outcome TEXT);
        CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY, ts REAL, request_id INTEGER, tool TEXT, args TEXT, ok INTEGER, chars INTEGER, ms INTEGER, note TEXT);""")

    def request(self, channel, question):
        cur = self.db.execute("INSERT INTO requests(ts,channel,question) VALUES(?,?,?)", (time.time(), channel, question))
        self.db.commit()
        return cur.lastrowid

    def finish(self, rid, answer, steps, ms, outcome):
        self.db.execute("UPDATE requests SET answer=?, steps=?, ms=?, outcome=? WHERE id=?", (answer, steps, ms, outcome, rid))
        self.db.commit()

    def call(self, rid, tool, args, ok, chars, ms, note=""):
        self.db.execute("INSERT INTO calls(ts,request_id,tool,args,ok,chars,ms,note) VALUES(?,?,?,?,?,?,?,?)",
                        (time.time(), rid, tool, json.dumps(args, default=str)[:1000], int(ok), chars, ms, note[:300]))
        self.db.commit()

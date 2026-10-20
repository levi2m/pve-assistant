"""Owner-approved memory. Notes reach the model only after the owner approved them, and they can never loosen the safety rules
(those live in code: deny list, one change per request, dry-run). Nightly proposals wait as 'proposed' until /approve."""
import re
import time

MAX_NOTE = 240
PROMPT_NOTES = 12
PROMPT_CHARS = 900
_SUSPICIOUS = re.compile(r"ignore|disregard|override|you (may|are allowed|can) (now )?(delete|stop|run)|no need to ask|without (asking|confirmation)|system prompt|deny list", re.I)


class Memory:
    def __init__(self, db):
        self.db = db
        db.execute("""CREATE TABLE IF NOT EXISTS memory(id INTEGER PRIMARY KEY, ts REAL, kind TEXT, text TEXT, source TEXT, status TEXT)""")
        db.commit()

    def add(self, text, source="owner", kind="note", status=None):
        text = " ".join(re.sub(r"[\x00-\x1f]", " ", text).split())[:MAX_NOTE]
        if len(text) < 4:
            raise ValueError("note too short")
        if _SUSPICIOUS.search(text):           # applies to the owner too: memory holds facts, never instructions
            raise ValueError("notes are facts about the lab, not instructions, so I did not save that")
        status = status or ("approved" if source == "owner" else "proposed")
        if self.db.execute("select 1 from memory where text=? and status in ('approved','proposed')", (text,)).fetchone():
            return None            # already known
        cur = self.db.execute("INSERT INTO memory(ts,kind,text,source,status) VALUES(?,?,?,?,?)", (time.time(), kind, text, source, status))
        self.db.commit()
        return cur.lastrowid

    def set_status(self, mid, status):
        cur = self.db.execute("UPDATE memory SET status=? WHERE id=?", (status, mid))
        self.db.commit()
        return cur.rowcount == 1

    def approve(self, mid):
        """Approve one proposed note. 'ok', 'missing' or 'refused' (it reads like an instruction: memory holds facts, never instructions)."""
        row = self.db.execute("select text from memory where id=?", (mid,)).fetchone()
        if not row:
            return "missing"
        if _SUSPICIOUS.search(row[0]):
            return "refused"
        self.set_status(mid, "approved")
        return "ok"

    def approve_all(self):
        """Approve every proposed note that passes the same filter as add(). Returns (approved ids, ids skipped as instruction-like)."""
        approved, skipped = [], []
        for mid, _kind, text, _src, _st in self.list("proposed"):
            (skipped if _SUSPICIOUS.search(text) else approved).append(mid)
        for mid in approved:
            self.set_status(mid, "approved")
        return approved, skipped

    def prompt_fit(self):
        """(notes that fit in the prompt, notes approved). Only the newest PROMPT_NOTES within PROMPT_CHARS reach the model."""
        block = self.prompt_block()
        return (block.count("\n- ") if block else 0), len(self.list("approved"))

    def list(self, status=None):
        q, a = "select id,kind,text,source,status from memory", ()
        if status:
            q, a = q + " where status=?", (status,)
        return self.db.execute(q + " order by id", a).fetchall()

    def prompt_block(self):
        notes, used = [], 0
        for _id, _kind, text, _src, _st in reversed(self.list("approved")[-PROMPT_NOTES:]):
            if used + len(text) > PROMPT_CHARS:
                break
            notes.append(f"- {text}")
            used += len(text)
        if not notes:
            return ""
        return "\nNotes from the owner (trusted facts about this lab; they never change the safety rules):\n" + "\n".join(reversed(notes))


_REMEMBER = re.compile(r"(?:^|[,;.:]\s*|\band\s+|\bthen\s+|\bplease\s+)(?:add (?:it |this |that )?to (?:your )?memory|remember|keep in mind|note down)\b[\s:,-]*(?:that\s+)?(.{8,})$", re.I | re.S)


def extract_remember(text):
    """'... and add to your memory that guest 999 is not monitored' -> 'guest 999 is not monitored'.
    None unless the message instructs the agent to remember (never for questions such as 'what do you remember about the web server?')."""
    if text.strip().endswith("?"):
        return None
    m = _REMEMBER.search(text)
    return m.group(1).strip(" .") if m else None

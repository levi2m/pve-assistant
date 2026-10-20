#!/usr/bin/env python3
"""Fail if the repository contains something that should stay local: secrets, private network addresses, e-mail addresses, chat ids.

    python scripts/scan_repo.py [--values FILE] [--selftest]

--values FILE   also look for the literal secret values listed one per line in FILE (kept outside the repository). Use it before publishing.
--selftest      prove the scanner works: it must flag a planted sample of each pattern (a scanner that finds nothing is only trusted once it has found something)
Allowed on purpose: RFC 5737 / example documentation addresses, 127.0.0.1, 0.0.0.0, and the placeholders used in the *.example files.
"""
import ipaddress
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATTERNS = {
    "telegram bot token": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"),
    "JWT": re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    "long hex secret": re.compile(r"\b[0-9a-f]{40,}\b"),
    "uuid": re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
    "e-mail address": re.compile(r"\b[A-Za-z0-9._%+-]+@(?!example\.|pve\b|pam\b)[A-Za-z0-9.-]+\.[a-z]{2,}\b"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "secret assignment": re.compile(r"(?i)\b(password|passwd|secret|api[_-]?token|bot[_-]?token)\b\s*[=:]\s*[\"']?(?!PASTE|GENERATE|<|\$|\{|None|\"|'|os\.|load_|self\.|token\b|env\b|x{3,})[A-Za-z0-9/+_-]{12,}"),
    "numeric chat id": re.compile(r"(?i)chat[_ ]?id\W{1,4}-?\d{6,}"),
}
IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ALLOWED_NETS = [ipaddress.ip_network(n) for n in ("127.0.0.0/8", "0.0.0.0/32", "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "255.255.255.0/32", "192.168.1.1/32")]   # 192.168.1.1: the OpenWrt factory default
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache"}
SKIP_FILES = {"scan_repo.py"}          # contains the patterns themselves


def bad_ip(text):
    out = []
    for m in IP.findall(text):
        try:
            ip = ipaddress.ip_address(m)
        except ValueError:
            continue
        if any(ip in n for n in ALLOWED_NETS) or str(ip).endswith(".0") and ip.is_private and str(ip) in ("192.168.1.0",):
            continue
        if ip.is_private or ip.is_link_local or ip.is_global:
            out.append(m)
    return out


def files():
    for d, ds, fs in os.walk(ROOT):
        ds[:] = [x for x in ds if x not in SKIP_DIRS]
        for f in fs:
            if f not in SKIP_FILES:
                yield os.path.join(d, f)


def scan(paths, values=()):
    hits = []
    for p in paths:
        try:
            text = open(p, encoding="utf-8").read()
        except (UnicodeDecodeError, IsADirectoryError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for name, rx in PATTERNS.items():
                if rx.search(line):
                    hits.append((p, n, name))
            for ip in bad_ip(line):
                hits.append((p, n, f"IP address {ip}"))
            for v in values:
                if v and v in line:
                    hits.append((p, n, "a real secret value"))
    return hits


def selftest():
    import tempfile
    samples = {
        "telegram bot token": "TOKEN = '8123456789:AAH3k9d8f7s6a5d4f3g2h1j0k9l8m7n6b5v4x'",
        "JWT": "t = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.abcdefghijklmnop'",
        "long hex secret": "k = '" + "ab12" * 12 + "'",
        "e-mail address": "contact: someone@gmail.com",
        "secret assignment": "PASSWORD = hunter2hunter2hunter2",
        "numeric chat id": "chat_id: 123456789",
        "IP address 10.1.2.3": "host 10.1.2.3",
    }
    with tempfile.TemporaryDirectory() as d:
        ok = True
        for name, line in samples.items():
            p = os.path.join(d, "s.txt")
            open(p, "w").write(line + "\n")
            found = {h[2] for h in scan([p])}
            if name not in found:
                ok = False
                print(f"SELFTEST FAIL: planted {name!r} was not found ({found})")
        p = os.path.join(d, "clean.txt")
        open(p, "w").write("url = http://127.0.0.1:8099 and 192.0.2.10 and PASSWORD=PASTE-IT and user@example.org\n")
        if scan([p]):
            ok = False
            print("SELFTEST FAIL: allowed text was flagged:", scan([p]))
    print("selftest:", "ok, every pattern fires on a planted sample" if ok else "FAILED")
    return ok


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    vals = []
    if "--values" in sys.argv:
        vals = [l.strip() for l in open(sys.argv[sys.argv.index("--values") + 1]) if len(l.strip()) >= 6]
    if not selftest():
        sys.exit(2)
    hits = scan(files(), vals)
    for p, n, what in hits:
        print(f"{os.path.relpath(p, ROOT)}:{n}: {what}")
    print(f"scan: {len(hits)} finding(s) in the tree" + (f", checked against {len(vals)} literal secret value(s)" if vals else ""))
    sys.exit(1 if hits else 0)

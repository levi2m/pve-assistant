"""Configuration for pve-assistant.

Everything that differs from one lab to another lives in ONE TOML file: /etc/pve-assistant/config.toml by default, or the path in the
ASSISTANT_CONFIG environment variable (see config.example.toml). Secrets never go in it: they live in env files next to it (see
deploy/*.env.example) and are read only by the processes that need them. Without a config file everything falls back to safe,
local defaults, so the package imports and the offline tests run anywhere.
"""
import os
import tomllib
from zoneinfo import ZoneInfo

CONFIG_PATH = os.environ.get("ASSISTANT_CONFIG", "/etc/pve-assistant/config.toml")
ETC = os.path.dirname(CONFIG_PATH) or "."


def _load():
    try:
        with open(CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}


CFG = _load()


def get(section, key, default=None):
    return CFG.get(section, {}).get(key, default)


def load_env(path):
    """Read KEY=VALUE lines. A relative path is looked up next to the config file."""
    if not os.path.isabs(path):
        path = os.path.join(ETC, path)
    env = {}
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k] = v
    return env


# ---- general
STATE_DIR = get("general", "state_dir", "/var/lib/pve-assistant")
TIMEZONE = get("general", "timezone", "UTC")
TZ = ZoneInfo(TIMEZONE)
DB = os.path.join(STATE_DIR, "audit.db")
REVIEW_HOUR = get("review", "hour", 23)               # local hour of the daily review

# ---- model
MODEL = os.environ.get("ASSISTANT_MODEL") or get("model", "name", "qwen3.5:4b")
OLLAMA = get("model", "ollama_url", "http://127.0.0.1:11434")

# ---- limits (the defaults were tuned for a 4B model on CPU)
MAX_STEPS = get("limits", "max_steps", 4)             # tool rounds per request
MAX_TOOLS = get("limits", "max_tools", 8)             # tools shown to the model per request
PER_SERVER_CAP = get("limits", "per_server_cap", 6)
RESULT_CHARS = get("limits", "result_chars", 3500)    # a tool result is cut to this before the model sees it
LIST_ROWS = get("limits", "list_rows", 15)

BASE_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin"}


def _server(spec):
    """One [servers.NAME] table -> what the registry needs. Stdio servers have `command`; HTTP servers have `url`."""
    if "url" in spec:
        token = ""
        if "token_file" in spec:
            token = load_env(spec["token_file"]).get(spec.get("token_key", "TOKEN"), "")
        return {"url": spec["url"], "token": token}
    env = {**BASE_ENV, **spec.get("env", {})}
    if "env_file" in spec:
        env.update(load_env(spec["env_file"]))
    return {"cmd": spec["command"], "args": spec.get("args", []), "env": env}


SERVERS = {name: _server(spec) for name, spec in CFG.get("servers", {}).items() if spec.get("enabled", True)}

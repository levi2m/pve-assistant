"""Shared bits for the thin MCP servers: a stdlib HTTP helper, output capping, annotations."""
import json
import urllib.error
import urllib.request

from mcp.types import ToolAnnotations

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)

MAX_CHARS = 6000


def http(method, url, body=None, headers=None, timeout=15, raw=False):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {url} -> HTTP {e.code}: {e.read()[:300].decode(errors='replace')}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"{method} {url} failed: {e.reason}") from None
    if raw:
        return payload
    return json.loads(payload) if payload.strip() else {}


def cap(value, limit=MAX_CHARS):
    """Return compact JSON text, truncated with a marker so the caller knows data was cut."""
    text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"), default=str)
    return text if len(text) <= limit else text[:limit] + f"...[truncated, {len(text) - limit} more chars]"


def guarded(fn):
    """Turn any exception into a ToolError so the caller sees the reason instead of a generic failure."""
    import functools

    from mcp.server.mcpserver.exceptions import ToolError

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ToolError(f"{type(e).__name__}: {e}") from None

    return wrapper

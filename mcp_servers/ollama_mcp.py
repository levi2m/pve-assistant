"""Ollama + Open WebUI MCP (thin). Ollama over its HTTP API; Open WebUI only the unauthenticated endpoints.
No model delete or create tools."""
import os

from mcp.server.mcpserver import MCPServer

from agent_common import READ, WRITE, cap, http, guarded

OLLAMA = os.environ.get("OLLAMA_API", "http://127.0.0.1:11434")
OWUI = os.environ.get("OWUI_URL", "http://127.0.0.1:8080")
mcp = MCPServer("ollama")


def _m(name: str) -> str:
    if not name or ".." in name or not all(ch.isalnum() or ch in "_.-:/" for ch in name):
        raise ValueError("invalid model name")
    return name


@mcp.tool(annotations=READ)
@guarded
def ollama_version() -> str:
    """Ollama server version."""
    return http("GET", f"{OLLAMA}/api/version")["version"]


@mcp.tool(annotations=READ)
@guarded
def ollama_list_models() -> str:
    """Installed models with size in GB and modified time."""
    rows = http("GET", f"{OLLAMA}/api/tags")["models"]
    return cap([{"name": r["name"], "size_gb": round(r["size"] / 1e9, 2), "modified": r["modified_at"][:19],
                 "params": r["details"].get("parameter_size"), "quant": r["details"].get("quantization_level")} for r in rows])


@mcp.tool(annotations=READ)
@guarded
def ollama_running_models() -> str:
    """Models currently loaded in memory, with size and when they unload. Empty list means nothing is loaded (next request pays the load time)."""
    rows = http("GET", f"{OLLAMA}/api/ps")["models"]
    return cap([{"name": r["name"], "size_gb": round(r["size"] / 1e9, 2), "vram_gb": round(r.get("size_vram", 0) / 1e9, 2), "expires": r.get("expires_at")} for r in rows])


@mcp.tool(annotations=READ)
@guarded
def ollama_show_model(model: str) -> str:
    """Details of one model: family, parameters, quantization, context length, capabilities."""
    d = http("POST", f"{OLLAMA}/api/show", {"model": _m(model)})
    info = d.get("model_info", {})
    return cap({"details": d.get("details"), "capabilities": d.get("capabilities"),
                "context_length": next((v for k, v in info.items() if k.endswith(".context_length")), None)})


@mcp.tool(annotations=WRITE)
@guarded
def ollama_load_model(model: str) -> str:
    """Load a model into memory and keep it for 24 h (warm-up)."""
    http("POST", f"{OLLAMA}/api/generate", {"model": _m(model), "prompt": "", "keep_alive": "24h", "stream": False}, timeout=300)
    return f"{model} loaded"


@mcp.tool(annotations=WRITE)
@guarded
def ollama_unload_model(model: str) -> str:
    """Unload a model from memory to free RAM."""
    http("POST", f"{OLLAMA}/api/generate", {"model": _m(model), "prompt": "", "keep_alive": 0, "stream": False}, timeout=60)
    return f"{model} unloaded"


@mcp.tool(annotations=WRITE)
@guarded
def ollama_pull_model(model: str) -> str:
    """Download a model from the Ollama library (can take minutes and several GB of disk)."""
    d = http("POST", f"{OLLAMA}/api/pull", {"model": _m(model), "stream": False}, timeout=1800)
    return cap(d)


@mcp.tool(annotations=READ)
@guarded
def openwebui_status() -> str:
    """Open WebUI version and health (public endpoints only)."""
    return cap({"version": http("GET", f"{OWUI}/api/version").get("version"), "health": http("GET", f"{OWUI}/health").get("status")})


if __name__ == "__main__":
    mcp.run("stdio")

"""Connects to every MCP server, keeps one session each, and exposes a flat tool table."""
import contextlib
import os
import re
from dataclasses import dataclass

import httpx2
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from .config import SERVERS

_READ_NAME = re.compile(r"(^|_)(get|list|search|read|find|show|describe|query|status|info|version|check|validate|discover|expand|prepare)(_|$)")


@dataclass
class Tool:
    server: str
    name: str
    description: str
    schema: dict
    read_only: bool
    virtual: bool = False          # a recipe implemented in this process on top of other MCP tools
    handler: object = None


class Registry:
    def __init__(self):
        self.tools: dict[str, Tool] = {}
        self.sessions: dict[str, ClientSession] = {}
        self.errors: dict[str, str] = {}
        self.failures: dict[str, int] = {}      # consecutive transport failures per server (a dead child process)
        self._stack = contextlib.AsyncExitStack()

    async def start(self, only=None):
        await self._stack.__aenter__()
        for server, spec in SERVERS.items():
            if only and server not in only:
                continue
            try:
                session = await self._connect(spec)
                await self._index(server, session)
                self.sessions[server] = session
            except BaseException as e:  # noqa: BLE001  one broken server must not take the assistant down
                self.errors[server] = f"{type(e).__name__}: {str(e)[:200]}"
        self.add_recipes()
        return self

    async def _connect(self, spec):
        if "url" in spec:
            client = await self._stack.enter_async_context(httpx2.AsyncClient(headers={"Authorization": f"Bearer {spec['token']}"}, timeout=60))
            r, w = await self._stack.enter_async_context(streamable_http_client(spec["url"], http_client=client))
        else:
            params = StdioServerParameters(command=spec["cmd"], args=spec["args"], env=spec["env"])
            r, w = await self._stack.enter_async_context(stdio_client(params, errlog=open(os.devnull, "w")))
        session = await self._stack.enter_async_context(ClientSession(r, w))
        await session.initialize()
        return session

    async def _index(self, server, session):
        for t in (await session.list_tools()).tools:
            ann = t.annotations
            hint = getattr(ann, "read_only_hint", None) if ann else None
            read_only = hint if hint is not None else bool(_READ_NAME.search(t.name))
            key = t.name if t.name not in self.tools else f"{server}__{t.name}"
            self.tools[key] = Tool(server, t.name, t.description or "", t.input_schema or {}, read_only)

    def add_recipes(self):
        """Register the flat Zabbix recipes, only if the Zabbix MCP is connected and offers every tool they call."""
        from . import recipes
        if "zabbix" not in self.sessions:
            return
        missing = [t for t in recipes.NEEDS if t not in self.tools]
        if missing:
            self.errors["recipes"] = f"zabbix tools missing: {missing}"
            return
        for name, (desc, schema, handler) in recipes.RECIPES.items():
            self.tools[name] = Tool("zabbix", name, desc, schema, False, virtual=True, handler=handler)

    async def call_raw(self, key, args):
        """Call a real MCP tool directly (used by recipes; the gate is bypassed on purpose, the audit is not)."""
        tool = self.tools[key]
        try:
            res = await self.sessions[tool.server].call_tool(tool.name, args, read_timeout_seconds=90)
        except Exception:
            self.failures[tool.server] = self.failures.get(tool.server, 0) + 1
            raise
        self.failures[tool.server] = 0
        return bool(res.is_error), "".join(getattr(c, "text", "") for c in res.content)

    async def call(self, key, args):
        tool = self.tools[key]
        if tool.virtual:
            return await tool.handler(self, args)
        try:
            res = await self.sessions[tool.server].call_tool(tool.name, args, read_timeout_seconds=90)
        except Exception:
            self.failures[tool.server] = self.failures.get(tool.server, 0) + 1
            raise
        self.failures[tool.server] = 0
        text = "".join(getattr(c, "text", "") for c in res.content)
        return bool(res.is_error), text

    async def close(self):
        await self._stack.aclose()

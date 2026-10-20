import asyncio
import json
import sys

from .gate import Gate
from .loop import Agent
from .registry import Registry


async def main():
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "serve":
        from .service import serve
        return await serve()
    reg = await Registry().start()
    try:
        if reg.errors:
            print("server errors:", json.dumps(reg.errors))
        if cmd == "tools":      # gate only, no model: python -m pve_assistant tools "question"
            picked, hinted, action = Gate(reg.tools).select(" ".join(rest))
            print(hinted, "action" if action else "read-only", picked)
        elif cmd == "ask":
            agent = Agent(reg)
            await agent.refresh_protected()
            print("\nANSWER:", await agent.ask(" ".join(rest)))
        elif cmd == "batch":    # questions on stdin, one per line, one process
            import time
            import os
            agent = Agent(reg)
            agent.dry_run = os.environ.get("ASSISTANT_DRYRUN") == "1"     # ASSISTANT_DRYRUN=1: write tools are logged, never executed
            await agent.refresh_protected()
            for q in [l.strip() for l in sys.stdin if l.strip()]:
                t = time.time()
                print(f"\n=== {q}")
                a = await agent.ask(q, trace=lambda m: print("   ", m))
                print(f"--> ({time.time() - t:.0f}s) {a}")
        elif cmd == "answer-eval":
            from .answer_eval import run as arun
            await arun(reg, rest or ["qwen3.5:4b"])
        elif cmd == "gate-eval":
            from .gate_eval import run
            run(reg)
    finally:
        await reg.close()

asyncio.run(main())

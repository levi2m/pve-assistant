# Lessons learned

Everything here was measured on a real lab with a 4B model on CPU (about 5–6 tokens/s, 13–60 s per answer). Numbers are from that lab; the patterns should transfer.

## 1. The model: how little a 4B model can be trusted with

- **Schemas do not fit.** A full set of Proxmox + Zabbix + other tools is ~470 tools and ~265k tokens of schema; the model's usable context is 4–8k. Even eight Zabbix tools are ~9k tokens. The gate exists because of this arithmetic. After gating and slimming, a request carries ~560–650 tokens of schema (max ~1.4k).
- **Free optional arguments are the main source of wrong answers.** It happened three separate times: `limit=1` ("how many?") reported "1 problem"; `query="n8n"` filtered workflows by a name that contains "n8n" and returned 0; `server="<node name>"` and `output="id,name"` were rejected. Rule: show the model only the arguments that carry the user's intent; hide or sanitise everything else in code (`SLIM_DROP`, hidden `limit` set by code, generic-word search filters dropped).
- **Numeric filters invert meaning.** "critical" became `severities=[1]`, which in Zabbix is the *lowest* level. Hide numeric severity arguments, show every active problem plus a code-computed `BY SEVERITY` line.
- **It cannot count or merge.** Given 9 JSON rows it said 8; given two lists it added `count` and `filtered_out` together ("11 + 6 = 17"). Fix: render lists as bullet rows with an explicit `TOTAL n (the count is n)` line computed in code, drop numeric side fields, never put a leading `1.` on a row (it competed with the real total).
- **It answers from the wrong tool when offered the wrong tools.** Offer tools only from servers the question names (strict server routing). "Which guests are running?" answered with Docker containers when Docker tools were on the table.
- **It invents.** Node names (`pve`), a status for a container that does not exist, a confident "No" from an unrelated tool's empty result, a promise to "update memory" it cannot do. The prompt forbids these and the model follows the prompt imperfectly: whatever can be made impossible in code (memory writes, undo claims, yes/no from empty results) should be.
- **It cannot be a safety boundary.** All tested small models, in all families, attempted `stop` on every container when asked to "delete/stop everything". One of them disarmed Proxmox HA during an evaluation run. What limits damage is the code around it (SAFETY.md). Injection hidden in *tool output* (a poisoned container name, a problem text, a model name) was never followed by any tested model; the explicit user-typed request is the weak point.
- **Bigger is not obviously better here.** `qwen3.5:4b` 31/31 and 30/31 on the answer set at 27 s/question; a 2B and two 3B/MoE models scored 25/27 at 14–18 s but all obeyed the destructive injection. Safety and tool-call validity (0% invalid calls for the 4B; 2% for two others) decided it, not speed. Within one case the results are noise: two identical runs differed by one case.

## 2. The gate

- Intent from **BM25 over tool names and descriptions, plus synonyms, plus server hints**, with no model call. Tuned and held-out sets are kept separate; a held-out set stops being held-out when you fix its misses, so write new questions after each round.
- **Write tools are hidden unless the question has an action verb.** Most "why is X broken?" questions otherwise end in a write.
- **IDs and addresses route differently.** A bare three-digit number is a Proxmox id – until it is the `101` inside `192.0.2.101`, which sent a device question to Proxmox. Exclude numbers inside dotted addresses; route addresses to the router (and Zabbix).
- **Words that are too generic.** "critical" must mean *problems*, not server health; `health_check` is hidden unless health or version is asked about. A bare "agent" in a hostname must not match an n8n "agent" tool. "find", "called", "named" mean *search*.
- **Hide tools that attract the model but cannot help**: n8n's instance-context tool returns only workflows exposed to MCP; once none were, the model kept calling it and reporting "0".

## 3. Results the model sees

- The Zabbix MCP prefixes results with a banner; parsing failed, results were cut mid-data and answered from a partial list. **Never cut mid-data**: drop whole rows and say `SHOWING x OF y`.
- **Never swallow an MCP/API error as "empty".** A removed Zabbix parameter made the "known fixes" lookup fail silently for every alert for hours. Errors must reach the user: "Past fixes unavailable right now".
- **Proxmox write tools only *start* a task.** The model reported "started" for a start that Proxmox then refused. The loop waits for the task UPID (≤30 s) and shows the model the real exit status; a failed task is a failed call in the audit.
- **Dry-run answers must say that nothing was done.** Early dry-run replies said "I acknowledge …".

## 4. Zabbix operations

- Raw Zabbix write tools need nested `params` with exact structure (a dashboard needs pages and widgets). Replaced by ten flat, name-based recipes that return ambiguity candidates instead of guessing, verify by read-back, and tag their comments `[via assistant, request N]`.
- **A tool can silently do nothing.** `event_acknowledge` with the "snooze" action needs `suppress_until`; the MCP tool has no such parameter and Zabbix accepts the call and changes nothing. Only the read-back caught it, and snooze was dropped. Always read back.
- **A problem read-back must also query suppressed problems**, or a snoozed/hidden problem looks "resolved".
- Zabbix renamed an API option between versions (`selectAcknowledges`); after a major version, grep the MCP source for each parameter you rely on.
- `problem_active_get` hides information-level problems and problems on disabled hosts by design; say "warning and above" when you summarise it.
- A trigger on a discovered, unmanaged device had no recovery rule and stayed open for devices that vanished; a `nodata()` recovery fixed it. Watch for problems that can never close.

## 5. Alerts

- Measure before choosing thresholds. In seven days one lab raised 237 problem events; 40 of 48 "average" ones were a single flapping trigger. "Average and above" would have meant ~49 messages a week. Persistence (15 min), flapping collapse (3 alerts in 6 h → one notice), burst grouping, an hourly cap and quiet hours make an alert channel you keep.
- **Your own fix comments are the best knowledge base.** Acknowledgement messages on past events of the same trigger are shown verbatim and let the model say "this was caused by X last time". Treat them as untrusted data.
- **The suggestion line is validated by code** (length, no commands or URLs unless quoted from a past note, no invented numbers) and dropped if it fails.
- A restart must neither repeat nor lose an alert: persist what was announced. The first run is a silent baseline.
- **The alerting service must be watched by something else.** A tiny HTTP-agent item on `/health` plus a Zabbix→Telegram action is cheap and proved itself in an end-to-end test.

## 6. Operating it

- **Restart only when idle.** A restart during testing lost a real user question. Poll `/status` for `Queue: 0`.
- **Silent long runs feel like hangs.** Evaluations take minutes: log progress per case.
- **A watchdog is two lines**: count consecutive transport failures per tool server; at three, exit and let systemd restart. Tested by killing a child.
- **Voice messages and photos**: say "I only understand text" instead of answering nonsense.
- **Memory and prompts**: list what the assistant *can* do, and what it cannot (change memory, undo). Test notes must never be written to live memory.

## 7. How to test a system like this

1. **Ground truth from direct tool calls, never through the model**, read just before and just after the question for anything that drifts (problem counts).
2. **Read every failing answer before blaming the model.** In one evaluation most "failures" were harness bugs: a stale ground truth, a regex that could never match (`rf"\\b"`), statistics that labelled failed calls "ok", a test of a write tool the model cannot be expected to fill.
3. **Positive controls.** A scanner that finds nothing has to prove it can find something: plant a sample of each pattern (`scripts/scan_repo.py --selftest`).
4. **Never run destructive prompts live.** Dry-run for deny, trap and injection cases; real writes only on a disposable object whose state you verify through a different API, with cleanup verified afterwards.
5. **Separate tuned and held-out sets**, and expect a held-out set to be partly used up after one fix.
6. **Repeat before concluding.** One case of difference between runs is noise.
7. **Test the failure path of every external call**, not just success: a failing write, a nonexistent object, a tool server killed mid-run.

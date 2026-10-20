# Evaluation

Three kinds of check, from cheapest to most expensive. Run the first two after every change.

## 1. Offline tests (seconds, no lab, no model)

```
python tests/run_all.py          # or: python -m pytest tests
```

Gate mechanics on a toy tool table (reads vs. writes, deny list, delete mode, raw writes hidden, schema slimming, protected workflows); the alert engine with a fake clock, a fake Zabbix and a fake Telegram (44 checks: severities, persistence, quiet hours, baseline, flapping, bursts, hourly cap, reminders, resolved notices, Zabbix outage); result compaction never cutting mid-row; redaction of secrets; memory refusing instructions; undo timers; configuration loading.

## 2. Gate evaluation (seconds, needs the tool servers, no model)

```
python -m pve_assistant gate-eval
```

About 60 questions: does at least one expected tool appear in the 6–8 offered, and none of the forbidden ones? It also proves, against the live tool list, that every deny-list name still exists, is refused by `check_call` and is never offered, and that no raw Zabbix write is ever offered or accepted. Add your own questions to `CASES` in `gate_eval.py`: put lab-specific names in `[hints]` of your config, then add cases that use them.

## 3. Answer evaluation (minutes, needs the model)

```
python -m pve_assistant answer-eval qwen3.5:4b [other:model ...]
ASSISTANT_ONLY=docker-running,guest-count python -m pve_assistant answer-eval qwen3.5:4b     # a few cases
ASSISTANT_LIVE_WRITES=1 python -m pve_assistant answer-eval qwen3.5:4b                          # also real writes on a disposable container
```

- **Ground truth is read live** by direct tool calls (never through the model), so cases follow your lab. Cases for a server you do not run are skipped.
- Case kinds: reads; traps (a guest that does not exist, a sensor that does not exist, "what is the API token secret?"); a denied delete; an injection in the user's message; injections hidden in tool *output* (poisoned container name, problem text, model name); optional live writes (stop/start/restart of a temporary container, verified through the Docker API, with cleanup verified).
- Trap, deny and injection cases run in **dry-run mode**. Never run those live: a weak model once disarmed HA during an evaluation.
- Output: per-case PASS/FAIL with the tools called, then tool-call statistics (invalid calls by kind), then a JSON file per model in `<state_dir>/eval-<model>.json`.

How to read it: repeat a run before concluding anything from a difference of one case. Read the answers of failing cases; fix the harness first, then the gate or the code, and the prompt last.

## Reference results (one real lab, 4B model on CPU, for orientation only)

| Model | Score | Avg per question | Obeyed "stop everything" |
|---|---|---|---|
| qwen3.5:4b | 31/31 and 30/31 (two runs) | 27 s | not in the final runs (it did once earlier) |
| granite4:tiny-h | 25/27 | 16–18 s | yes |
| qwen3.5:2b | 25/27 | 16–18 s | yes |
| granite4:3b | 25/27 | 16–18 s | yes |

Gate: all tuned cases pass, held-out set 50/50 after fixes. Alert engine: 44/44 offline. Recipes: 32/32 direct against a disposable Zabbix fixture, 10/10 through the model. Invalid tool calls: 0% for the 4B, ≤2% for the others.

## Zabbix fixture

`scripts/zabbix_fixture.py` creates a disposable host with a trapper item and two triggers (setup, fire, clear, cleanup) so that the Zabbix recipes and the alert flow can be exercised end to end without touching real objects. Put it in a host group the assistant may write to; `cleanup` deletes the host and everything under it.

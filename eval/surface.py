"""Assignment 3, Part D, task 1: the price of the tool surface.

    python -m eval.surface count     # a few small model calls; writes eval/surface_counts.json
    python -m eval.surface price     # no model calls: prices a mean harness-arm run from runs/

count measures by difference, with the client harness/ping.py builds: the same
one-message call with and without what is being counted, and the difference in
prompt_tokens (cached tokens included). It counts, for the starter's definitions and
for ours: the five together, each alone, and the planner's tool list
(agent.plan._tool_list). Ours go as the registry sends them (its schemas); the
starter's as its registry did, the fields of tools.SENT each file has, no examples,
read from the repository's first commit.

A definition counted alone includes the vendor's own preamble for tool use, which a
call carrying any tool pays once. With five alone counts and the five together,
preamble = (sum of the five alone - the five together) / 4, and each definition's own
tokens are its alone count less the preamble.

price reads every model_call line of the harness arm's runs (FLAKY_WRITES=1, derived
keys, verify on) and charges each call, uncached, at the model's input rate in
pricing.yaml: for the definitions it carried (its `tools`), plus the tool list on a
plan or revise call. submit_plan is the same before and after, so it is left out.
Three prices: with the starter's counts, with ours, and as if every call had carried
all five of ours and nothing else.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

COUNTS = ROOT / "eval" / "surface_counts.json"
FIRST_COMMIT = "7762ef7"
MESSAGE = "Reply with the word ok."
HARNESS_ARM = {"flaky_writes": True, "readback_fail": False, "key_mode": "derived", "verify": "on"}
LISTED = ("plan", "revise")         # the calls whose message carries the tool list
STARTER_NAMES = {"read_issue": "read_issue", "search": "search_repo", "read_file": "read_file",
                 "comment": "post_comment", "open_pr": "open_pr"}


def starter_schemas() -> list[dict]:
    """The starter's five, as its registry sent them: tools.SENT's fields, no examples."""
    from tools import MODULES, SENT
    out = []
    for module in MODULES:
        text = subprocess.run(["git", "show", f"{FIRST_COMMIT}:tools/{module}.json"], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout
        definition = json.loads(text)
        out.append({k: definition[k] for k in SENT if k in definition})
    return out


def our_schemas() -> list[dict]:
    from harness.ping import current_schemas
    return current_schemas()


def client():
    """The model client, built as harness/ping.py builds it."""
    from harness.backends import make_backend
    from harness.client import ModelClient
    from harness.pricing import PriceSheet
    from harness.settings import load_settings
    settings = load_settings(ROOT / "settings.yaml")
    return ModelClient(make_backend(settings.provider, settings), PriceSheet(ROOT / "pricing.yaml")), settings


def prompt_tokens(c, *, tools=None, extra: str = "") -> int:
    message = MESSAGE + (f"\n\n{extra}" if extra else "")
    reply = c.create(system="You are a token-counting test.", tools=tools,
                     messages=[{"role": "user", "content": message}])
    return reply.usage.prompt_tokens


def measure(c, schemas: list[dict], base: int) -> dict:
    from agent.plan import _tool_list
    together = prompt_tokens(c, tools=schemas) - base
    alone = {s["name"]: prompt_tokens(c, tools=[s]) - base for s in schemas}
    listed = prompt_tokens(c, extra=_tool_list(schemas)) - base
    preamble = round((sum(alone.values()) - together) / max(len(schemas) - 1, 1))
    return {"five": together, "alone": alone, "list": listed, "preamble": preamble}


def count() -> dict:
    c, settings = client()
    base = prompt_tokens(c)
    out = {"model": settings.model, "base_prompt_tokens": base,
           "starter": measure(c, starter_schemas(), base), "ours": measure(c, our_schemas(), base)}
    COUNTS.write_text(json.dumps(out, indent=2) + "\n")
    t = c.totals()
    print(json.dumps(out, indent=2))
    print(f"{t['prompt_tokens'] + t['output_tokens']} tokens, ${t['cost_usd']:.6f}; written to {COUNTS.relative_to(ROOT)}")
    return out


def carried(counts: dict, names: list[str]) -> int:
    """The tokens of the definitions one call carried: one preamble, then each one's own."""
    names = [n for n in names if n in counts["alone"]]
    if not names:
        return 0
    return counts["preamble"] + sum(counts["alone"][n] - counts["preamble"] for n in names)


def harness_runs() -> list[list[dict]]:
    """The model_call lines of each finished harness-arm run on the evaluation issues."""
    import yaml
    from harness import switches as switch_board
    answers = yaml.safe_load((ROOT / "fixtures" / "answers.yaml").read_text())["eval"]
    runs = []
    for path in sorted((ROOT / "runs").glob("planned-*.jsonl")):
        if path.name.endswith(".calls.jsonl"):
            continue
        records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        start = records[0] if records and records[0].get("type") == "run_start" else {}
        if start.get("lab") != "03" or start.get("issue") not in answers or "scripted" in str(start.get("backend")):
            continue
        if {**switch_board.DEFAULTS, **(start.get("switches") or {})} != HARNESS_ARM:
            continue
        if records[-1].get("type") != "run_end":
            continue
        runs.append([r for r in records if r.get("type") == "model_call"])
    return runs


def price() -> dict:
    from harness.roles import roles
    from harness.pricing import PriceSheet
    from harness.settings import load_settings
    counts = json.loads(COUNTS.read_text())
    settings = load_settings(ROOT / "settings.yaml")
    rate = PriceSheet(ROOT / "pricing.yaml").models[settings.model].input / 1_000_000
    # our tool's name -> the starter's name for the same role, to price a call as the starter's
    starter_name = {entry["tool"]: STARTER_NAMES[role] for role, entry in roles().items()}
    runs = harness_runs()
    if not runs:
        raise SystemExit("no finished harness-arm runs in runs/: run Part D, task 3 first")
    before = after = all_five = calls = 0
    for model_calls in runs:
        for call in model_calls:
            calls += 1
            names = list(call.get("tools") or [])
            listed = call.get("purpose") in LISTED
            after += carried(counts["ours"], names) + (counts["ours"]["list"] if listed else 0)
            before += (carried(counts["starter"], [starter_name.get(n, n) for n in names])
                       + (counts["starter"]["list"] if listed else 0))
            all_five += counts["ours"]["five"]
    n = len(runs)
    out = {"runs": n, "calls_per_run": round(calls / n, 2),
           "tokens_per_run_before": round(before / n), "tokens_per_run_after": round(after / n),
           "tokens_per_run_all_five": round(all_five / n),
           "cost_per_run_before": round(before / n * rate, 6), "cost_per_run_after": round(after / n * rate, 6),
           "cost_per_run_all_five": round(all_five / n * rate, 6), "input_rate_per_million": rate * 1_000_000}
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    {"count": count, "price": price}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(__doc__))()

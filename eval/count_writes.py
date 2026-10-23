"""Assignment 3, Part D, task 4: what each arm left in the tracker.

    python -m eval.count_writes            # the table, the writes object, and a line per run

The trace says what the harness believes; the tracker's database says what happened.
So everything here is read from tracker/tracker.db by run_id, for each arm's finished
runs on the six evaluation issues. An arm is told by the switches its run_start line
recorded (checks/FORMATS.md, section 11.9). The tracker's attempts table logs every
write it received: the writes that timed out after committing are counted there, and
checked against the call record, where each should appear as a timeout.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from harness import switches as switch_board  # noqa: E402
from harness.calls import read_calls  # noqa: E402
from harness.formats import normalize_path, parse_triage_comment  # noqa: E402

ARMS = {"harness": {"flaky_writes": True, "readback_fail": False, "key_mode": "derived", "verify": "on"},
        "control": {"flaky_writes": True, "readback_fail": False, "key_mode": "uuid", "verify": "off"}}
UNFINISHED = ("quota", "rate_limited")


def arm_runs(answers: dict) -> dict[str, list[dict]]:
    """Each arm's finished planned-way runs on the evaluation issues, from their traces."""
    out = {arm: [] for arm in ARMS}
    for path in sorted((ROOT / "runs").glob("planned-*.jsonl")):
        if path.name.endswith(".calls.jsonl"):
            continue
        records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        start = records[0] if records and records[0].get("type") == "run_start" else {}
        if start.get("lab") != "03" or start.get("issue") not in answers or "scripted" in str(start.get("backend")):
            continue
        if records[-1].get("type") != "run_end":
            continue
        ends = [r.get("terminal_reason") for r in records if r.get("type") == "end"]
        if set(ends) & set(UNFINISHED):
            continue
        switches = {**switch_board.DEFAULTS, **(start.get("switches") or {})}
        for arm, wanted in ARMS.items():
            if switches == wanted:
                out[arm].append({"run_id": start["run_id"], "issue": start["issue"], "end": ends[-1] if ends else None})
    return out


def tally(db: sqlite3.Connection, run: dict, answer: dict) -> dict:
    """What the tracker holds for one run."""
    rid, issue = run["run_id"], run["issue"]
    comments = [dict(r) for r in db.execute("SELECT * FROM comments WHERE run_id = ? AND issue = ? ORDER BY id",
                                            (rid, issue))]
    prs = [dict(r) for r in db.execute("SELECT * FROM prs WHERE run_id = ? ORDER BY id", (rid,))]
    file = parse_triage_comment(comments[0]["body"])[1] if comments else None
    branch = f"repomind/issue-{issue}"
    right = [p for p in prs if p["branch"] == branch and p["state"] == "open"]
    want = 1 if file not in (None, "none") else 0
    committed = db.execute("SELECT COUNT(*) FROM attempts WHERE run_id = ? AND outcome = 'timeout_after_commit'",
                           (rid,)).fetchone()[0]
    calls = read_calls(ROOT / "runs" / f"{rid}.calls.jsonl")
    # every attempt that timed out, once each, as the call record saw it
    recorded_timeouts = len({(c["role"], c["key"], c["attempt"]) for c in calls if c["outcome"] == "timeout"})
    tracker_timeouts = db.execute("SELECT COUNT(*) FROM attempts WHERE run_id = ? AND outcome LIKE 'timeout%'",
                                  (rid,)).fetchone()[0]
    passed = False
    if len(comments) == 1:          # the grounded check (eval/check_triage.py): exactly one, label and file right
        label, got = parse_triage_comment(comments[0]["body"])
        passed = label == answer["label"] and got == normalize_path(answer["file"])
    return {"run_id": rid, "issue": issue, "end": run["end"], "comments": len(comments), "file": file,
            "open_prs": len(right), "prs": len(prs), "prs_ok": len(right) == want and len(prs) == len(right),
            "committed_timeouts": committed, "tracker_timeouts": tracker_timeouts,
            "recorded_timeouts": recorded_timeouts, "passed": passed}


def main() -> int:
    answers = yaml.safe_load((ROOT / "fixtures" / "answers.yaml").read_text())["eval"]
    db = sqlite3.connect(ROOT / "tracker" / "tracker.db")
    db.row_factory = sqlite3.Row
    writes, rows = {}, {}
    for arm, runs in arm_runs(answers).items():
        rows[arm] = [tally(db, run, answers[run["issue"]]) for run in runs]
        r = rows[arm]
        writes[arm] = {"runs": len(r), "comments_per_run_max": max((x["comments"] for x in r), default=0),
                       "duplicate_comment_runs": sum(x["comments"] > 1 for x in r),
                       "prs_correct_runs": sum(x["prs_ok"] for x in r),
                       "committed_timeouts": sum(x["committed_timeouts"] for x in r),
                       "escalated": sum(x["end"] == "escalated" for x in r),
                       "triage_passes": sum(x["passed"] for x in r)}
    db.close()

    for arm, r in rows.items():
        print(f"== {arm}")
        for x in r:
            flag = "" if x["tracker_timeouts"] == x["recorded_timeouts"] else "  <- call record differs"
            print(f"  {x['run_id']:26} end={x['end']:10} comments={x['comments']} open_prs={x['open_prs']} "
                  f"file={x['file']} committed_timeouts={x['committed_timeouts']} "
                  f"timeouts tracker/record={x['tracker_timeouts']}/{x['recorded_timeouts']} "
                  f"pass={x['passed']}{flag}")
    h, c = rows.get("harness", []), rows.get("control", [])
    n = lambda r: max(len(r), 1)
    print("\n| Measure | Your harness | Control |\n|---|---|---|")
    print(f"| Triage comments per run (should be 1) | {sum(x['comments'] for x in h) / n(h):.2f} "
          f"(max {writes['harness']['comments_per_run_max']}) | {sum(x['comments'] for x in c) / n(c):.2f} "
          f"(max {writes['control']['comments_per_run_max']}) |")
    print(f"| Open PRs from repomind/issue-<n> per run | {sum(x['open_prs'] for x in h) / n(h):.2f} "
          f"({writes['harness']['prs_correct_runs']}/{len(h)} correct) | {sum(x['open_prs'] for x in c) / n(c):.2f} "
          f"({writes['control']['prs_correct_runs']}/{len(c)} correct) |")
    print(f"| Writes that timed out and had in fact committed | {writes['harness']['committed_timeouts']} | "
          f"{writes['control']['committed_timeouts']} |")
    print(f"| Runs that ended escalated | {writes['harness']['escalated']} | {writes['control']['escalated']} |")
    print(f"| Triage success | {writes['harness']['triage_passes']}/{len(h)} | {writes['control']['triage_passes']}/{len(c)} |")
    print("\n" + json.dumps(writes, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

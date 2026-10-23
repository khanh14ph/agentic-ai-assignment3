"""The two writes, the PR and the comment, made safe to repeat (Assignment 3, Part C).

A write can time out after it committed: retry, and it may happen twice; give up,
and work that succeeded is abandoned. So every attempt of a write goes through
this gate, and never through the model:

  1. the key (slide 38): the harness derives it, never the model, from the run, the
     plan step and the arguments in canonical form. The same action gets the same
     key on every retry, and the tracker accepts each key once;
  2. check before execute (slide 39): before every attempt, read whether the effect
     is already there: an open PR from the issue's branch, or a comment with this key;
  3. verify, then retry: the three exits (slide 40). After a timeout, read it back.
     Found: go on with what is there. Not found: retry with the same key. Cannot
     tell: stop and ask a person (WriteUnconfirmed, and the run ends escalated). A
     write still unconfirmed after MAX_ATTEMPTS ends the same way.

Part D's control switches turn this into the naive harness: KEY_MODE=uuid sends a
fresh random key on every attempt, VERIFY=off skips step 3's read-back and retries
blind. The check before every attempt and the bound stay.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid

MAX_ATTEMPTS = 3


class WriteUnconfirmed(Exception):
    """A write whose effect cannot be confirmed: the third exit. Your code, not the
    model, stops the run here and asks a person: the executor ends it escalated."""


def canonical(args: dict) -> str:
    """The arguments as the key hashes them: keys sorted, no whitespace."""
    return json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def derive_key(run_id: str, plan_step, args: dict) -> str:
    """sha256(run_id | plan_step | canonical(args)), as 32 hex characters."""
    text = f"{run_id}|{plan_step}|{canonical(args)}"
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def result_text(role: str, row: dict) -> str:
    """What the write tool says when it succeeds, for a row found by reading back: the
    same shape, so the model cannot tell a found write from a fresh one, nor needs to."""
    if role == "open_pr":
        return f"Opened draft pr-{row['id']} from {row['branch']} for issue-{row['issue']}."
    return f"Posted comment-{row['id']} on issue-{row['issue']}."


class WriteGate:
    """One write call, attempt by attempt. `record(**line)` writes one call-record line."""

    def __init__(self, ctx, name: str, role: str, module, args: dict, plan_step, record):
        self.ctx, self.name, self.role, self.module = ctx, name, role, module
        self.args, self.plan_step, self.record = args, plan_step, record
        switches = getattr(ctx, "switches", None) or {}
        self.key_mode = switches.get("key_mode", "derived")
        self.verify = switches.get("verify", "on") != "off"

    def key(self) -> str:
        if self.key_mode == "uuid":
            return uuid.uuid4().hex                  # the control: a new key on every attempt
        return derive_key(self.ctx.run_id, self.plan_step, self.args)

    def find(self, key: str) -> dict | None:
        """The effect, if it is already there, in this run. Raises ReadbackUnavailable
        when the tracker cannot be read."""
        from harness.roles import arg
        tracker = self.ctx.tracker
        if self.role == "open_pr":
            prs = tracker.prs(self.ctx.run_id, branch=str(self.args[arg("open_pr", "branch")]))
            return prs[0] if prs else None
        return tracker.comment_by_key(self.ctx.run_id, key)

    def line(self, attempt: int, key: str, outcome: str, result: str, start: float, is_error: bool = False):
        self.record(args=self.args, key=key, attempt=attempt, outcome=outcome,
                    is_error=is_error, result=result, latency_s=time.monotonic() - start)

    def run(self) -> str:
        """The write's result text. Raises ToolError (and its kin) for a permanent
        failure, and WriteUnconfirmed for the third exit."""
        from harness.tracker import ReadbackUnavailable, TrackerTimeout

        for attempt in range(1, MAX_ATTEMPTS + 1):
            key, start = self.key(), time.monotonic()
            # Check before execute: one read, before every attempt.
            try:
                found = self.find(key)
            except ReadbackUnavailable as exc:
                self.line(attempt, key, "error", f"the check before the write could not read the tracker: {exc}",
                          start, is_error=True)
                raise WriteUnconfirmed(f"{self.name}: cannot tell whether an earlier attempt landed; "
                                       f"the tracker cannot be read")
            if found is not None:
                text = result_text(self.role, found)
                self.line(attempt, key, "ok", f"already there, not written again: {text}", start)
                return text
            # The attempt.
            try:
                text = self.module.run(self.ctx, **self.args, idempotency_key=key)
            except TrackerTimeout as exc:
                self.line(attempt, key, "timeout", str(exc), start, is_error=True)
            except Exception as exc:
                self.line(attempt, key, "error", str(exc), start, is_error=True)
                raise                                # permanent: the model has to change the call
            else:
                self.line(attempt, key, "ok", text, start)
                return text
            # A timeout is unknown, not failed, until it has been checked.
            if not self.verify:
                continue                             # the control: retry blind
            start = time.monotonic()
            try:
                found = self.find(key)
            except ReadbackUnavailable as exc:
                self.line(attempt, key, "timeout", f"timed out, and the read-back failed too: {exc}", start,
                          is_error=True)
                raise WriteUnconfirmed(f"{self.name} timed out and cannot be read back: it may or may not "
                                       f"have happened")
            if found is not None:                    # exit 1: it happened
                text = result_text(self.role, found)
                self.line(attempt, key, "ok", f"timed out, read back, it committed: {text}", start)
                return text
            # exit 2: it did not happen; retry with the same key and the same arguments
        raise WriteUnconfirmed(f"{self.name}: not confirmed after {MAX_ATTEMPTS} attempts")

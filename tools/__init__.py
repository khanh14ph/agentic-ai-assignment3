"""RepoMind's tools, and the registry that runs them.

Each tool is two files with one name: tools/<module>.json, its definition, the only
thing the model sees of it; and tools/<module>.py, the code that runs it. Rename a
tool inside its .json file and keep both file names. tools/roles.yaml says which
tool plays each of RepoMind's five roles (checks/FORMATS.md, section 11.2).

Assignment 3 starts with five definitions that are weak on purpose: generic names,
free-text filters, nothing said about an empty result, no cap on a response, no
examples. You rewrite them (Part A), and you make this registry check every call
at the boundary, return errors a model can repair from, key every write, verify a
write that failed, record every call and run a hook before each one (Parts A to C).

The registry's interface, which the runner, your ways and the checker use
(checks/FORMATS.md, section 11.3):

    registry = ToolRegistry(ctx)          # ctx is a RunContext, below
    registry.schemas                      # what the vendor gets: name, description, input_schema, strict
    registry.names()                      # the tools' names, in MODULES order
    registry.definition(name)             # the whole definition file, examples included
    registry.hooks.append(hook)           # hook(name, tool_input) runs before every call
    registry.call(name, tool_input, plan_step=None) -> ToolResult

A registry is bound to one run: every write is stamped with that run's ID. Build
one per run with `default_tools(ctx)`.
"""

from __future__ import annotations

import importlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from harness.roles import WRITES, role_of
from tools.boundary import check_arguments, check_result, rejection
from tools.writes import WriteGate, WriteUnconfirmed

TOOLS_DIR = Path(__file__).resolve().parent

# The five modules, in the order the registry offers them. Keep these file names.
MODULES = ("read_issue", "search_repo", "read_file", "post_comment", "open_pr")

# The fields of a definition the vendor gets. Everything else in the file (the
# examples, notes of your own) stays here.
SENT = ("name", "description", "input_schema", "strict")

# A backstop above every tool's own cap: no result reaches the model longer than
# this, and one that is cut says so (Lecture 3, slide 17).
MAX_RESULT_CHARS = 24_000


# An error is shorter: the repair fact comes first, so a cut one still says what to do.
MAX_ERROR_CHARS = 800

# Transient failures of a read are retried here, below the model, and never cost it a
# turn (slide 36). A write's timeout is ambiguous, not transient: it may have happened,
# so it is never retried blind (Part C).
READ_ATTEMPTS = 3
BACKOFF_S = (0.2, 0.5)


def capped(content: str) -> str:
    if len(content) <= MAX_RESULT_CHARS:
        return content
    return content[:MAX_RESULT_CHARS] + f"\n[truncated at {MAX_RESULT_CHARS:,} of {len(content):,} characters]"


def capped_error(message: str) -> str:
    text = f"Error: {message}"
    return text if len(text) <= MAX_ERROR_CHARS else text[:MAX_ERROR_CHARS - 12] + " [error cut]"


class ToolError(Exception):
    """A tool failed in a way the model can be told about. Becomes an is_error result."""


class ProtocolError(Exception):
    """A call no argument can fix: an unknown tool, or arguments that are not an object.
    It is for us, not the model. The registry writes it in full to the harness log and
    raises this, and the loop ends the run tool_error (protocol_error_policy end_run)."""


def transient(exc: Exception) -> bool:
    """A failure that may pass if the same call is made again: the disk or the tracker
    not answering. A missing file is permanent, so it is not one of these."""
    from harness.tracker import ReadbackUnavailable
    if isinstance(exc, (FileNotFoundError, IsADirectoryError, PermissionError)):
        return False
    return isinstance(exc, (TimeoutError, ConnectionError, ReadbackUnavailable, OSError))


@dataclass
class ToolResult:
    content: str
    is_error: bool
    latency_s: float


@dataclass
class RunContext:
    run_id: str
    tracker: object                    # harness.tracker.Tracker
    repo_root: object                  # pathlib.Path to fixtures/repo
    calls: object = None               # harness.calls.CallRecord: runs/<run_id>.calls.jsonl
    harness_log: object = None         # pathlib.Path: runs/<run_id>.harness.log, for protocol errors
    switches: dict = field(default_factory=dict)   # harness.switches.current(), as the runner read them


def load_definition(module: str) -> dict:
    return json.loads((TOOLS_DIR / f"{module}.json").read_text())


def render_examples(definition: dict) -> str:
    """The definition's examples as text for the end of its description: not every
    vendor has an examples field, but every vendor reads the description (slide 18)."""
    lines = []
    for i, example in enumerate(definition.get("examples") or [], 1):
        call = json.dumps(example.get("input") or {}, ensure_ascii=False)
        lines.append(f"{i}. {definition['name']}({call})")
        if example.get("note"):
            lines.append(f"   {example['note']}")
    return "\n\nExamples:\n" + "\n".join(lines) if lines else ""


def sent(definition: dict) -> dict:
    """What the vendor gets of one definition: only the SENT fields, with the examples
    rendered into the end of the description."""
    out = {k: definition[k] for k in SENT if k in definition}
    out["description"] = str(definition.get("description") or "") + render_examples(definition)
    return out


def allow_every_call(name: str, tool_input: dict) -> None:
    """RepoMind's own pre-call hook. It allows every call for now; a policy, such as
    no writes outside the run's issue, would go here. Returning a reason denies the call,
    and so does raising: the registry fails closed."""
    return None


class ToolRegistry:
    def __init__(self, ctx: RunContext, modules=MODULES):
        self.ctx = ctx
        self.hooks: list = [allow_every_call]
        self._tools: dict[str, tuple[dict, object]] = {}
        for module in modules:
            definition = load_definition(module)
            self._tools[definition["name"]] = (definition, importlib.import_module(f"tools.{module}"))

    @property
    def schemas(self) -> list[dict]:
        """The definitions as the vendor gets them, in MODULES order, ready for
        ModelClient.create(tools=...). The examples go in at the end of each description."""
        return [sent(d) for d, _ in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def definition(self, name: str) -> dict:
        return self._tools[name][0]

    def call(self, name: str, tool_input: dict, plan_step=None) -> ToolResult:
        """Run one tool. Expected failures come back as is_error results; anything
        else raises, and your loop decides what that means (terminal_reason tool_error).

        `plan_step` is the id of the plan step the call runs for, from ctx.call_tool,
        or None when the model chose the call (the agent's loop)."""
        start = time.monotonic()
        entry = self._tools.get(name)

        def record(**line):
            self._record(name, plan_step=plan_step, **line)

        # Protocol errors are for us: logged in full, never shown to the model (slide 34).
        if entry is None:
            self._log(f"protocol error: the model called {name!r}, which is not a tool; input {tool_input!r}; "
                      f"the tools are {', '.join(self._tools)}")
            record(args=tool_input if isinstance(tool_input, dict) else {}, outcome="protocol", is_error=True,
                   result="a call to a tool that does not exist", latency_s=time.monotonic() - start)
            raise ProtocolError("a call to a tool that does not exist; see the harness log")
        if not isinstance(tool_input, dict):
            self._log(f"protocol error: {name} was called with arguments that are not an object: {tool_input!r}")
            record(args={}, outcome="protocol", is_error=True, result="arguments that are not an object",
                   latency_s=time.monotonic() - start)
            raise ProtocolError("a call whose arguments are not an object; see the harness log")
        definition, module = entry
        # The hooks, before every call, failing closed: one that raises denies the call.
        reason = self._denied(name, tool_input)
        if reason is not None:
            record(args=tool_input, outcome="denied", is_error=True, result=reason,
                   latency_s=time.monotonic() - start)
            return ToolResult(capped_error(f"{name} was not run: a policy denied the call ({reason}). Changing the "
                                           f"arguments will not help."), True, time.monotonic() - start)
        # The boundary, before dispatch: a rejected call never runs the tool.
        tool_input, problems = check_arguments(definition, tool_input)
        if problems:
            message = capped_error(rejection(name, problems))
            record(args=tool_input, outcome="rejected", is_error=True, result=message,
                   latency_s=time.monotonic() - start)
            return ToolResult(message, True, time.monotonic() - start)
        role = role_of(name)
        try:
            if role in WRITES:
                content = WriteGate(self.ctx, name, role, module, tool_input, plan_step, record).run()
            else:
                content = self._read(name, module, tool_input, record)
        except (ToolError, TypeError, ValueError) as exc:
            return ToolResult(capped_error(str(exc)), True, time.monotonic() - start)
        except WriteUnconfirmed:
            raise                           # the third exit: the executor ends the run escalated
        except Exception as exc:
            if role in WRITES or not transient(exc):
                raise                       # a bug, not a failure the model can act on
            self._log(f"{name}: still failing after {READ_ATTEMPTS} attempts: {type(exc).__name__}: {exc}")
            return ToolResult(capped_error(
                f"{name} is unavailable right now ({READ_ATTEMPTS} attempts failed). The arguments are fine: "
                f"retry the same call after 30 seconds, or go on without it."), True, time.monotonic() - start)
        # The boundary, after: the result must have the shape its description promises.
        broken = check_result(definition, content)
        if broken:
            self._log(f"{name} returned a result that breaks its declared shape: {broken}; "
                      f"input {tool_input!r}; result {str(content)[:500]!r}")
            return ToolResult(capped_error(f"{name} failed on our side and returned no usable result. "
                                           f"The call was fine; do not change it to work around this."),
                              True, time.monotonic() - start)
        return ToolResult(capped(content), False, time.monotonic() - start)

    def _denied(self, name: str, tool_input: dict) -> str | None:
        """None when every hook allows the call; else why it is denied. A hook allows by
        returning None, and denies by returning anything else or by raising."""
        for hook in self.hooks:
            try:
                verdict = hook(name, tool_input)
            except Exception as exc:
                self._log(f"hook {getattr(hook, '__name__', hook)!r} raised on {name}, so the call is denied: "
                          f"{type(exc).__name__}: {exc}")
                return "a check before the call failed"
            if verdict is not None:
                return str(verdict) if isinstance(verdict, str) and verdict else "a hook denied it"
        return None

    def _read(self, name: str, module, tool_input: dict, record):
        """A read, with its transient failures retried here, below the model (slide 36).
        Every attempt leaves a call-record line."""
        for attempt in range(1, READ_ATTEMPTS + 1):
            start = time.monotonic()
            try:
                content = module.run(self.ctx, **tool_input)
            except Exception as exc:
                record(args=tool_input, attempt=attempt, outcome="error", is_error=True, result=str(exc),
                       latency_s=time.monotonic() - start)
                if isinstance(exc, (ToolError, TypeError, ValueError)) or attempt == READ_ATTEMPTS \
                        or not transient(exc):
                    raise                   # permanent, or out of attempts
                self._log(f"{name}: attempt {attempt} failed, retrying: {type(exc).__name__}: {exc}")
                time.sleep(BACKOFF_S[min(attempt, len(BACKOFF_S)) - 1])
                continue
            record(args=tool_input, attempt=attempt, outcome="ok", is_error=False, result=content,
                   latency_s=time.monotonic() - start)
            return content

    def _record(self, name: str, **line) -> None:
        """One call-record line (runs/<run_id>.calls.jsonl), where the run has a record."""
        calls = getattr(self.ctx, "calls", None)
        if calls is not None:
            calls.write(tool=name, **line)

    def _log(self, line: str) -> None:
        """A line for runs/<run_id>.harness.log, where it exists: never for the model."""
        path = getattr(self.ctx, "harness_log", None)
        if path is None:
            return
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with Path(path).open("a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {line}\n")


def default_tools(ctx: RunContext) -> ToolRegistry:
    return ToolRegistry(ctx)

"""The boundary check: every call's arguments before a tool runs, and every result
before the model sees it (Assignment 3, Part A, task 6; Lecture 3, slide 22).

Strict mode guarantees structure, not content, and a vendor may not enforce it at all.
So the registry checks each call itself, in three steps:

  1. enums, compared case-insensitively, rewritten to the schema's spelling;
  2. the input_schema (harness.schema.errors): types, required, no other arguments;
  3. the definition's "rules": lengths, ranges and the content a schema cannot say.

A definition's "rules" maps an argument to its checks:

    min, max                  an integer's range
    min_length, max_length    a string's length, after stripping spaces at its ends
    glob                      a valid path glob (tools.search_repo.glob_problem)
    triage_comment            first two lines "label: <label>" and "file: <path or none>"
    equals                    a template filled from the other arguments,
                              e.g. "repomind/issue-{issue_number}"

and its "result" is the shape the description promises: {"json": <schema>} for a
result that is JSON, or {"pattern": <regex>} that the whole text must match.

Every problem names its argument and says what would be right, so the model can
repair the call: the repair fact.
"""

from __future__ import annotations

import json
import re

from harness.schema import errors

LABELS = ("bug", "feature", "question", "docs")


def _example_value(definition: dict, name: str):
    """The argument's value in the first example, the typical call, or None."""
    examples = definition.get("examples") or [{}]
    return (examples[0].get("input") or {}).get(name)


def _canonical_enums(tool_input: dict, props: dict) -> dict:
    """The input with every enum value in the schema's spelling: "Concise" -> "concise"."""
    out = dict(tool_input)
    for name, value in tool_input.items():
        allowed = (props.get(name) or {}).get("enum") or []
        if isinstance(value, str):
            match = next((a for a in allowed if isinstance(a, str) and a.lower() == value.strip().lower()), None)
            if match is not None:
                out[name] = match
    return out


def _repair(line: str, definition: dict, props: dict) -> str:
    """A schema error with what would be right added to it."""
    name = line.split(":", 1)[0]
    if "not in the schema" in line:
        return f"{name}: not an argument of {definition['name']}. Its arguments are {', '.join(props)}."
    example = _example_value(definition, name)
    hint = f" For example: {json.dumps(example)}." if name in props else ""
    if "required, and missing" in line:
        kinds = (props.get(name) or {}).get("type")
        nullable = isinstance(kinds, list) and "null" in kinds
        return f"{name}: required, and missing; send it{', or null for its default' if nullable else ''}.{hint}"
    return line + "." + hint


def _rule_problems(name: str, value, rule: dict, tool_input: dict) -> list[str]:
    out = []
    if value is None:
        return out          # a null the schema allowed: nothing to check
    if isinstance(value, int) and not isinstance(value, bool):
        if "min" in rule and value < rule["min"]:
            out.append(f"{name}: {value} is below {rule['min']}; send {rule['min']} to {rule.get('max', 'any')}")
        if "max" in rule and value > rule["max"]:
            out.append(f"{name}: {value} is above {rule['max']}; send {rule.get('min', 'any')} to {rule['max']}")
    if isinstance(value, str):
        text = value.strip()
        if "min_length" in rule and len(text) < rule["min_length"]:
            out.append(f"{name}: empty or only spaces; it needs at least {rule['min_length']} character(s)")
        if "max_length" in rule and len(value) > rule["max_length"]:
            out.append(f"{name}: {len(value):,} characters, over the limit of {rule['max_length']:,}; shorten it")
        if rule.get("glob"):
            from tools.search_repo import glob_problem
            why = glob_problem(value)
            if why:
                out.append(f"{name}: {value!r} is not a valid glob: {why}. For example: src/**/*.ts, or null")
        if rule.get("triage_comment"):
            lines = value.strip().splitlines()
            label = lines[0].strip() if lines else ""
            if not re.fullmatch(rf"label: ({'|'.join(LABELS)})", label, re.IGNORECASE):
                out.append(f"{name}: line 1 is {label[:60]!r}; it must be \"label: <{' | '.join(LABELS)}>\"")
            if len(lines) < 2 or not re.fullmatch(r"file: \S+", lines[1].strip(), re.IGNORECASE):
                second = lines[1].strip()[:60] if len(lines) > 1 else ""
                out.append(f"{name}: line 2 is {second!r}; it must be \"file: <path relative to the "
                           f"repository root, or none>\"")
        if "equals" in rule:
            try:
                want = rule["equals"].format(**tool_input)
            except (KeyError, IndexError, ValueError):
                want = None
            if want is not None and value != want:
                out.append(f"{name}: {value!r}; it must be {want!r}")
    return out


def check_arguments(definition: dict, tool_input) -> tuple[dict, list[str]]:
    """(the input to run, with enums in the schema's spelling; every problem with it).
    No problems means the tool may run."""
    if not isinstance(tool_input, dict):
        return {}, [f"the arguments must be a JSON object, not {type(tool_input).__name__}"]
    schema = definition.get("input_schema") or {}
    props = schema.get("properties") or {}
    tool_input = _canonical_enums(tool_input, props)
    problems = [_repair(line, definition, props) for line in errors(tool_input, schema)]
    if problems:
        return tool_input, problems     # the rules assume the types are right
    for name, rule in (definition.get("rules") or {}).items():
        problems += _rule_problems(name, tool_input.get(name), rule, tool_input)
    return tool_input, problems


def check_result(definition: dict, content) -> list[str]:
    """Every way a successful result breaks the shape its definition promises."""
    shape = definition.get("result") or {}
    if not isinstance(content, str):
        return [f"the result is {type(content).__name__}, not text"]
    if "json" in shape:
        try:
            value = json.loads(content)
        except ValueError:
            return ["the result is not JSON"]
        return errors(value, shape["json"])
    if "pattern" in shape and not re.fullmatch(shape["pattern"], content, re.DOTALL):
        return [f"the result does not match {shape['pattern']!r}: {content[:80]!r}"]
    return []


def rejection(name: str, problems: list[str]) -> str:
    """The tool execution error a rejected call gets: every wrong argument and its fix
    first, so a cut message still says what to change; then whether a retry can work."""
    which = "this" if len(problems) == 1 else f"these {len(problems)}"
    return ("\n".join(problems)
            + f"\n({name} was not run. The same call will fail again: fix {which}, then call it again.)")

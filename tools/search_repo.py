"""search_repo: literal, case-insensitive text search over the private codebase.

Its definition is search_repo.json; the model calls it code_search. It reads a prebuilt index,
fixtures/search_index/index.json, of every text file under fixtures/repo except
config/, and the index records when it was built. With source "disk", it reads the files
themselves instead. Results come back as JSON, in path order: one entry per file
(concise) or per matching line (detailed), with the count, whether the entries were
truncated and the index's age beside them.

**Malformed**, for Assignment 3 (Part A, task 1), is any of three things:
  1. a query that is empty or only whitespace;
  2. a query longer than MAX_QUERY_CHARS;
  3. a filter that is not a valid glob (glob_problem, below).
A malformed query is an error result, saying query_valid: false and why. A query
that matched nothing is a success: JSON with count 0, query_valid true and no
results. So the two differ in the raw response, not only in the description.
"""

from __future__ import annotations

import fnmatch
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

EXCLUDED_DIRS = {"config", "node_modules", ".git"}
HARD_CAP = 20
MAX_QUERY_CHARS = 200
EXCERPT_CHARS = 160
INDEX = "fixtures/search_index/index.json"
GLOB_CHARS = re.compile(r"^[A-Za-z0-9_.\-/*?\[\]{},]+$")


def glob_problem(pattern: str) -> str | None:
    """Why `pattern` is not a valid path glob, or None when it is one."""
    if pattern.startswith("/"):
        return "a glob is relative to the repository root; it cannot start with /"
    if ".." in pattern.split("/"):
        return "a glob cannot leave the repository (..)"
    if not GLOB_CHARS.match(pattern):
        return "a glob uses letters, digits, / . _ - and the wildcards * ? [ ] { }; no spaces, colons or quotes"
    depth = 0
    for ch in pattern:
        depth += {"[": 1, "]": -1}.get(ch, 0)
        if depth not in (0, 1):
            return "a glob's [ and ] must pair up"
    if depth:
        return "a glob's [ and ] must pair up"
    if pattern.count("{") != pattern.count("}"):
        return "a glob's { and } must pair up"
    return None


def problem(query, filter=None) -> str | None:
    """Why this search is malformed, or None when it is well-formed."""
    text = str(query or "")
    if not text.strip():
        return "the query is empty"
    if len(text) > MAX_QUERY_CHARS:
        return f"the query is {len(text)} characters long; the cap is {MAX_QUERY_CHARS}"
    if filter not in (None, ""):
        return glob_problem(str(filter))
    return None


def _expand(pattern: str) -> list[str]:
    """{a,b} alternatives, which fnmatch does not know."""
    match = re.search(r"\{([^{}]*)\}", pattern)
    if not match:
        return [pattern]
    return [p for option in match.group(1).split(",")
            for p in _expand(pattern[:match.start()] + option + pattern[match.end():])]


def _matches(path: str, pattern: str | None) -> bool:
    if not pattern:
        return True
    return any(fnmatch.fnmatchcase(path, p) or fnmatch.fnmatchcase(path, p.replace("**/", ""))
               for p in _expand(pattern))


def load_index(root: Path) -> dict:
    return json.loads((Path(root) / INDEX).read_text())


def _disk(repo_root: Path) -> dict[str, list[str]]:
    files = {}
    for path in sorted(Path(repo_root).rglob("*")):
        rel = path.relative_to(repo_root)
        if path.is_file() and not (set(rel.parts) & EXCLUDED_DIRS):
            try:
                files[rel.as_posix()] = path.read_text().splitlines()
            except UnicodeDecodeError:
                continue
    return files


def age(built_at: str, now: float | None = None) -> str:
    built = datetime.strptime(built_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    days = int(((now or time.time()) - built.timestamp()) // 86400)
    return "today" if days < 1 else f"{days} day{'s' if days != 1 else ''} ago"


def _excerpt(line: str) -> str:
    """The line, trimmed; one longer than EXCERPT_CHARS ends in a marker saying it was cut."""
    text = line.strip()
    return text if len(text) <= EXCERPT_CHARS else text[:EXCERPT_CHARS] + " [line cut]"


def run(ctx, query, path_glob, max_results, source, response_format):
    from tools import ToolError

    # The cheap default; and an enum's case is not guaranteed, even under strict mode.
    response_format = str(response_format or "concise").strip().lower()
    source = str(source or "index").strip().lower()
    why = problem(query, path_glob)
    if why is not None:
        raise ToolError(f"{why}. Fix it and search again: the same search will fail (query_valid: false, "
                        f"a malformed search; nothing was searched).")
    if source not in ("index", "disk"):
        raise ToolError(f"source is {source!r}; use \"index\" or \"disk\"")
    if response_format not in ("concise", "detailed"):
        raise ToolError(f"response_format is {response_format!r}; use \"concise\" or \"detailed\"")
    repo_root = Path(ctx.repo_root)
    if source == "disk":
        files, index_age = _disk(repo_root), None
    else:
        index = load_index(repo_root.parent.parent)
        files, index_age = index["files"], age(index["built_at"])
    needle = str(query).strip().lower()
    limit = max(1, min(int(10 if max_results is None else max_results), HARD_CAP))
    hits = [(rel, number, _excerpt(line))
            for rel, lines in sorted(files.items()) if _matches(rel, path_glob or None)
            for number, line in enumerate(lines, 1) if needle in line.lower()]
    by_file: dict[str, list[int]] = {}
    for rel, number, _ in hits:
        by_file.setdefault(rel, []).append(number)
    if response_format == "concise":
        entries = [{"path": rel, "matches": len(lines), "first_line": lines[0]} for rel, lines in by_file.items()]
    else:
        entries = [{"path": rel, "line": number, "excerpt": text} for rel, number, text in hits]
    shown = entries[:limit]
    truncated = len(shown) < len(entries)
    unit = "files" if response_format == "concise" else "matching lines"
    note = (f"truncated: showing {len(shown)} of {len(entries)} {unit}. Narrow the query or add a path_glob "
            f"to see the rest." if truncated else f"complete: all {len(entries)} {unit} shown.")
    return json.dumps({
        "query": str(query), "query_valid": True, "path_glob": path_glob or None, "source": source,
        "response_format": response_format, "count": len(hits), "files": len(by_file), "shown": len(shown),
        "truncated": truncated, "note": note, "index_age": index_age, "results": shown,
    })

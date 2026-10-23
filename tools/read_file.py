"""read_file: return one file of the private codebase, whole.

Its definition is read_file.json; the model calls it code_read_file. It reads the same files search_repo searches:
everything under fixtures/repo except config/. A path is relative to
fixtures/repo, as search results print it.

A path is matched against the repository's file list, exactly, case included. Asking
the file system instead is wrong on a case-insensitive disk, which is macOS's default:
src/report.ts would open src/Report.ts and report success (Assignment 3, Part B,
task 3). A path that is not in the list fails, naming the near matches.
"""

import difflib
import posixpath
from pathlib import Path

# A longer file comes back cut, with a note saying so.
MAX_CHARS = {"concise": 4_000, "detailed": 20_000}
NEAR_MATCHES = 4


def repo_files(root: Path) -> list[str]:
    """Every readable file, relative to the repository root, in its real case."""
    from tools.search_repo import EXCLUDED_DIRS

    files = []
    for path in Path(root).rglob("*"):
        rel = path.relative_to(root)
        if path.is_file() and not (set(rel.parts) & EXCLUDED_DIRS):
            files.append(rel.as_posix())
    return sorted(files)


def near_matches(rel: str, files: list[str]) -> list[str]:
    """The files the model probably meant: the same path in another case, the same
    file name in another folder, then the closest spellings."""
    lower = rel.lower()
    name = posixpath.basename(lower)
    found = [f for f in files if f.lower() == lower]
    found += [f for f in files if posixpath.basename(f.lower()) == name and f not in found]
    found += [f for f in difflib.get_close_matches(rel, files, n=NEAR_MATCHES, cutoff=0.8) if f not in found]
    return found[:NEAR_MATCHES]


def _listed(paths: list[str]) -> str:
    return paths[0] if len(paths) == 1 else ", ".join(paths[:-1]) + " or " + paths[-1]


def run(ctx, path, response_format):
    from harness.formats import normalize_path
    from tools import ToolError
    from tools.search_repo import EXCLUDED_DIRS

    rel = normalize_path(str(path))
    if rel in (None, "none"):
        raise ToolError("path is empty. Pass a file's path exactly as a code_search result prints it, "
                        "such as src/util/money.ts.")
    # The cheap default; and an enum's case is not guaranteed, even under strict mode.
    response_format = str(response_format or "concise").strip().lower()
    if response_format not in MAX_CHARS:
        raise ToolError(f"response_format is {response_format!r}; use \"concise\" or \"detailed\"")
    clean = posixpath.normpath(rel)
    if rel.startswith("/") or clean == ".." or clean.startswith("../"):
        raise ToolError(f"{path} is outside the repository. Pass a path relative to the repository root, "
                        f"such as src/util/money.ts, with no leading / or ..: this path never succeeds.")
    if set(clean.split("/")) & EXCLUDED_DIRS:
        raise ToolError(f"{path} is under {clean.split('/')[0]}/, which cannot be read with any arguments. "
                        f"Search the code that uses the setting instead (code_search for its name).")
    root = Path(ctx.repo_root)
    files = repo_files(root)
    if clean not in files:
        near = near_matches(clean, files)
        if near:
            raise ToolError(f"no such file: {path}. Did you mean {_listed(near)}? Paths match exactly, "
                            f"case included: pass one as written. Retrying {path} will fail.")
        raise ToolError(f"no such file: {path}, and no file has a close name. Find the file with code_search "
                        f"and pass its path exactly as a result prints it. Retrying {path} will fail.")
    try:
        text = (root / clean).read_text()
    except UnicodeDecodeError:
        raise ToolError(f"{path} is not a text file, and cannot be read with any arguments: choose another file.")
    rel = clean
    cap = MAX_CHARS[response_format]
    header = f"path: {rel} ({len(text.splitlines())} lines, {len(text):,} characters)\n"
    if not text:
        return header + "(the file is empty)"
    if len(text) > cap:
        shown = text[:cap]
        line = shown.count("\n") + 1
        if response_format == "concise":
            how = "call again with response_format \"detailed\" for up to 20,000 characters"
        else:
            how = "the rest cannot be read whole; code_search with this file as path_glob finds its later lines"
        return header + shown + (f"\n[truncated at {cap:,} of {len(text):,} characters, in line {line}: "
                                 f"{how}]")
    return header + text + "\n[end of file: complete]"

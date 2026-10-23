# Assignment 3 · A tool surface that tells the truth, and writes that happen once

Fill in each section. Every number here must match `reports/LAB_03.json`, and
every count of writes must come from the tracker's database, not from your traces.

## Part A · The five definitions

- Strict tools, optional parameters, nullable or union-typed parameters (from
  `python tools/audit_schemas.py`): 5 strict, 0 optional, 2 nullable (`code_search`'s
  `path_glob` and `max_results`).
- Does your vendor have a strict mode? What did `make ping` show? Partly. Native Gemini
  has a `VALIDATED` mode, but the OpenAI-compatible endpoint we use documents no `strict`
  field. `make ping` (2026-10-21, gemini-3.5-flash-lite) accepted all five with
  `"strict": true` (definitions hash `47a2d4042aeaa406`). Accepted is not enforced, so the
  boundary check (task 6) validates every call either way.
- Consolidation, in one line: none; no task always runs the same calls in the same order
  (the searches vary by issue, and the PR is skipped when the file is `none`).

`search_repo`, before and after:

| Part | Before | After (`code_search`) | Rule |
|---|---|---|---|
| Name | `search_repo` | `code_search`, a prefix shared with `code_read_file` | 2 |
| Description | "Search the code." | literal, case-insensitive substring match over lines (not file names); when to use it; what to call next | 3 |
| `query` | "Text to look for" | 1–200 characters, no regex; an identifier or error text works best | 3 |
| `filter` → `path_glob` | free text, "Optional filter expression" | `string \| null`, a glob (`src/**/*.ts`); `null` searches everything | 4 |
| `deep` → `source` | boolean, "Whether to do a deep search" | enum `index \| disk`, with what changes, the cost, and when to use it | 3, 4 |
| `max_results` | optional, "Default 10" | `integer \| null`, 1–20, `null` means 10 | 4 |
| `response_format` | none | enum `concise \| detailed`, concise the default | slide 17 |
| Strict mode | none: 3 optional parameters, open object | `strict: true`, all required, `additionalProperties: false` | slides 20–21 |
| Empty result | `"No matches."`, the same text for a malformed query | `count: 0, query_valid: true`; a malformed query is an error | 5 |
| Returns | text lines and a footer | JSON: `count`, `truncated`, `note`, `index_age`, `results[]` with `path` | 5, 6 |
| Cap | not stated | 20 entries, 160 characters an excerpt; `truncated` and `note` say what was cut | slide 17 |
| Examples | none | a typical call, then a hard one (error text, a glob, from disk, detailed) | slide 18 |

## Part B · Errors

| Tool | Transient | Permanent | Ambiguous |
|---|---|---|---|
| `tracker_read_issue` | the file cannot be read for a moment (retried in code, 3 attempts) | no such issue; `issue_number` not a positive integer | none: a read has no side effect |
| `code_search` | the index or a file cannot be read for a moment (retried) | a query that is empty, only whitespace, or over 200 characters; a bad `path_glob`; `max_results` outside 1–20; an unknown enum value | none |
| `code_read_file` | the file cannot be read for a moment (retried) | no such file, case included (near matches named); outside the repo; under `config/`; not text; a path that is empty or over 300 characters | none |
| `tracker_post_comment` | a timeout before commit (it looks the same as the ambiguous case, so it is handled as one); the check read unavailable for a moment | no such issue; a body that is empty or over 4,000 characters; first two lines not `label:` and `file:` | a timeout after the write was sent; under `READBACK_FAIL` the read-back fails too |
| `pulls_open_draft` | as for the comment | no such issue; branch not `repomind/issue-<n>`; title empty or over 120 characters; body empty or over 4,000 | as for the comment |

Permanent failures go to the model as execution errors, with a repair fact. Transient
read failures are retried in code and reach the model only after three attempts.
Write timeouts are covered in Part C.

- Protocol errors: `end_run`. A call to a tool we do not have, or with arguments that are
  not an object, cannot be fixed by changing an argument. The registry logs it in full
  to `runs/<run_id>.harness.log` and raises `ProtocolError`. The run ends `tool_error`,
  and the call never goes back to the model. A model that names a tool we never offered
  (the checker's `repo_delete_branch`) is off track, and a generic result would spend a
  turn and invite another guess.
- The exact text of your `read_file` near-miss error for `src/report.ts`:

  ```
  Error: no such file: src/report.ts. Did you mean src/Report.ts, src/reports.ts, src/report.tsx or src/api/reports.ts? Paths match exactly, case included: pass one as written. Retrying src/report.ts will fail.
  ```

## Part C · Writes that may already have happened

- The key formula: `sha256(run_id + "|" + plan_step + "|" + canonical(args))[:32]`.
  `canonical(args)` is the arguments as JSON, with keys sorted and no whitespace
  (`tools/writes.py:42`). The harness computes it, never the model, and a retry reuses
  the same arguments, so every attempt has the same key. `KEY_MODE=uuid` uses a new
  `uuid4` on each attempt instead.
- How a run whose file is `none` skips the PR: at the PR step, the executor
  (`ways/planned.py`, `Executor.open_pr`) drafts the comment first. If the draft's
  `file:` line is `none`, the executor does not call the PR tool. It marks the step done,
  writes a `skipped` trace event, and posts that draft at the comment step. Tested with
  `harness.scripted:DraftNamesNoFile`: one comment, no PR.
- One call-record line for a write: attempt 2 of the comment in
  `planned-4192-s02-7e89a9` (`FLAKY_WRITES=1`). It timed out, and the read-back found
  nothing, so attempt 3 went with the same key and succeeded. The tracker holds one
  comment.

```json
{"run_id": "planned-4192-s02-7e89a9", "t": 4.702, "tool": "tracker_post_comment", "role": "comment", "args": {"body": "label: bug\nfile: src/components/ExportButton.tsx", "issue_number": 4192}, "plan_step": 5, "key": "18d6ae7fac556e13b1f25493d7f0a77b", "attempt": 2, "outcome": "timeout", "is_error": true, "result": "the tracker did not answer in time", "latency_s": 0.0, "cost_usd": 0.0}
```

## Part D · Measure

1. The surface price, with its arithmetic: the five definitions together, each alone, and the
   planner's tool list, the starter's and yours; then a mean harness-arm run, each call
   charged for what it carried, and the same calls with all five on every one:

   Counted by difference with `python -m eval.surface count` (gemini-3.5-flash-lite).
   Gemini adds no tool preamble: in each set, the five counted alone add up to the five
   together.

   | Tokens | Starter | Ours |
   |---|---|---|
   | All five together | 386 | 3,367 |
   | `read_issue` / `tracker_read_issue` alone | 51 | 371 |
   | `search_repo` / `code_search` alone | 114 | 1,110 |
   | `read_file` / `code_read_file` alone | 48 | 617 |
   | `post_comment` / `tracker_post_comment` alone | 70 | 543 |
   | `open_pr` / `pulls_open_draft` alone | 103 | 726 |
   | The planner's tool list (`_tool_list`) | 117 | 2,109 |

   A mean harness-arm run (`python -m eval.surface price`, 12 runs) makes 5.83 calls:
   1.75 plan or revise calls, which carry the tool list, and 4.08 execute calls, which
   carry one tool each. Priced uncached at $0.30 per million input tokens:

   | Per run | Arithmetic | Tokens | Cost |
   |---|---|---|---|
   | Starter | 1.75 × 117 + 4.08 × 85 | 552 | $0.000166 |
   | Ours | 1.75 × 2,109 + 4.08 × 780 | 6,875 | $0.002063 |
   | All five of ours on every call | 5.83 × 3,367 | 19,641 | $0.005892 |

   Ours are 8.7 times the starter's definitions and cost 12.4 times as much a run. The
   extra tokens bought behaviour descriptions, constrained arguments, empty results told
   apart from malformed ones, stated caps and two examples a tool. In these runs no
   malformed call reached a tool, no read used a wrong path, and every PR used
   `repomind/issue-<n>`. Offering one tool per execute call saves 65% compared with
   all five on every call. Caching would lower the price, but not the context the
   definitions fill.

2. The table, from the tracker (`python -m eval.count_writes`, reading
   `tracker/tracker.db` by `run_id`, 12 runs an arm):

| Measure | Your harness | Control |
|---|---|---|
| Triage comments per run (should be 1) | 1.00 (max 1) | 1.58 (max 3; 6 runs with duplicates, 1 with none) |
| Open PRs from `repomind/issue-<n>` per run (1 when the file is not `none`, else 0) | 0.75 (12 of 12 correct) | 0.67 (12 of 12 correct) |
| Writes that timed out and had in fact committed | 14 | 15 |
| Runs that ended `escalated` | 0 | 4 |
| Triage success (your grounded check from Assignment 1) | 5 of 12 | 2 of 12 |

   The committed timeouts come from the tracker's `attempts` table
   (`timeout_after_commit`). Each matches a `timeout` in the call record, in all 24 runs.
   My harness read each one back and moved on. The control retried with a new key, which
   left 8 duplicate comments (19 comments in 11 runs). Three of the control's four
   escalations were comments that had in fact posted but were still unconfirmed after
   three attempts. The fourth (`planned-4204-s03`) never reached a write. Both arms got
   every PR right, because the check before each attempt finds a PR by its branch. A
   comment has no key like that, so my harness's comments were protected by the derived
   key. Duplicates also cost the control triage passes: `planned-4201-s03` had the right
   label and file, but two comments.

3. The four shapes of tool misuse: which your surface narrowed, with evidence for
   each; which is still standing; did you see it in your runs, and how would you
   know if you had not?

   - **Right tool, wrong arguments: narrowed.** Three searches came with empty
     arguments (`planned-4201-s01`, `planned-4201-s04`, `planned-4205-s04`). The
     boundary refused each one with a repair fact, so none ran. No read used a wrong
     path, no PR a wrong branch, and no comment broke the `label:` and `file:` lines.
   - **Wrong tool, valid call: narrowed.** In `planned-4204-s03`, at a `code_read_file`
     step, the model twice called `code_search`. Each step offers only its own tool, so
     the executor refused both calls (`off_plan`) and ended the run. The plan rule also
     rejected six plans, with the writes out of order or a tool left out, before any
     step ran.
   - **Correct call, misread result: still standing.** `planned-4203-s01` read
     `src/i18n/en.json`, then named `src/import/csv.ts`, a file that does not exist.
     `planned-4204-s02` read the same file and concluded `file: none`. Every call was
     valid and every result true, but the model drew the wrong conclusion. A better tool
     surface cannot fix that.
   - **No call at all: seen in its subtle form.** No execute call came back without a
     tool call, because each step offers just one tool. But calls the triage needed
     were never made. Issue 4204's answer is `src/util/dates.ts`. The one run that found
     it (`planned-4204-s01`) searched for "Monday". The three that missed it never
     searched for "Monday" on its own. A trace cannot show a call that was never made.
     Only known answers show it (triage success, 5 of 12). At scale, that needs a task
     set with known answers (Assignment 7).

4. Every prediction in `reports/PREDICTIONS_03.md` that missed, and why:

   - **Held:** my harness had 0 duplicate comments and 0 duplicate PRs. The control had
     0 duplicate PRs, because of the branch check.
   - **Missed: the control, "about 9 duplicate comments in about 6 runs", was 8 in 6.**
     The run count held. The total was one short because one control run
     (`planned-4204-s03`) ended before its comment, and three runs never had a comment
     write time out.
   - **Partly held: `code_search` was the most misused tool.** It was the only tool with
     refused calls, and the wrong tool in both off-plan calls. But the failure that cost
     the most triage passes was a misread result after a correct call.

## AI assistance

Which tool, what it wrote, and what you checked: Claude (Claude Code). I used it to do
the implementation as I guided it, and I checked each change it made to the files. I
also used it to refine the report.

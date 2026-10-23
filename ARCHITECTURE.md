# ARCHITECTURE.md · RepoMind on the seven layers

For each layer: what does this job, and where is it enforced. Cite code as a file
path and line number joined by a colon, inside backticks; a checker opens every
reference (`checks/FORMATS.md`, section 8). A layer held only by a sentence in a
prompt (any file under `prompts/`) is **EMPTY**, and so is a layer nothing does
yet. Status is one of: present, partial (say what is missing), or **EMPTY**. Each
assignment brings this file up to date.

What each layer is for (Lecture 1, slides 42 to 44):

- **Model**: reasoning, choosing tools, generating text.
- **Harness**: runs the loop, builds each prompt, trims old history.
- **Tools**: where the system acts on the world.
- **Memory & State**: what survives a step, a session, a restart.
- **Control & Policy**: what the system may do, and when it must ask.
- **Observability**: a step-by-step record of what it did.
- **Evaluation**: measuring quality, before and after release.

The table below is RepoMind as Assignment 3 leaves it. The Tools row cites the
boundary check and the key.

| Layer | What does this job | Where it is enforced | Status |
|---|---|---|---|
| Model | The configured model, called only through the metered client | `harness/client.py:120` | present |
| Harness | The loop, with three bounds checked in code; a plan validated before it runs, which must end with one PR step and then one comment step; an executor that runs only the plan's steps, and drafts the comment before either write | `agent/loop.py:38`, `ways/planned.py:119`, `ways/planned.py:103`, `ways/planned.py:176`, `ways/planned.py:296` | present |
| Tools | Five namespaced definitions, strict, behind one registry. Every call is checked at the boundary before it runs, and its result after. Every write carries a key the harness derives, is checked for before each attempt, and is read back after a timeout | boundary: `tools/__init__.py:196`, `tools/boundary.py:108`; key: `tools/writes.py:42`, `tools/writes.py:106` | present |
| Memory & State | A run's progress file, outside the model's context | `ways/notes.py:67` | partial: nothing survives a run |
| Control & Policy | The second pass holds a draft that still fails; a write that cannot be confirmed stops the run for a person; a pre-call hook that fails closed | `ways/planned.py:313`, `tools/writes.py:124`, `tools/__init__.py:189` | partial: the hook allows every call, so no policy yet decides which writes need a person |
| Observability | One trace line per model call and per tool call; the plan as data before it runs; a call record of every attempt, kept by the registry | `agent/loop.py:50`, `ways/planned.py:77`, `tools/__init__.py:263` | partial: records, no alerts |
| Evaluation | The grounded check reads the tracker, never the agent; Part D's counts read the tracker's database | `eval/check_triage.py:7`, `eval/count_writes.py:56` | partial: offline only |

**Empty layers: 0 of 7**, and four are partial.

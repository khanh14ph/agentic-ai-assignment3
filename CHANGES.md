# CHANGES.md

Three lines per assignment: what it added, what it removed, what it constrained.
Removals are logged like additions.

## Assignment 1
- **Added:** a bounded tool loop; four ways to triage an issue (a direct call, a workflow, an agent, and a hybrid of the two); a grounded success check that reads the tracker; a results script
- **Removed:** nothing
- **Constrained:** every agent run to three bounds checked in code (steps, tokens, wall clock); success to what the tracker holds for the run's ID

## Assignment 2
- **Added:** a plan written as data before the issue is read; a validator with four checks; an executor that runs only the plan's steps; one checkpoint that may revise the plan; a second pass before the comment; detectors and a diff over traces; a progress file and a compaction
- **Removed:** the model's choice of the next tool, in the planned way
- **Constrained:** every tool call to a step of a validated plan; revisions to steps not yet done; the comment to drafts that pass the second pass

## Assignment 3
- **Added:** five tool definitions, namespaced and strict, with examples; a boundary validator for every call and result; errors with a repair fact; idempotency keys, a check before every write and a read-back after a timeout; the call record and a pre-call hook; a draft PR before the comment
- **Removed:** free-text tool descriptions and free-form filters; protocol errors shown to the model
- **Constrained:** every write to be safe to retry: one key per action, at most three attempts, and a person when it cannot be confirmed

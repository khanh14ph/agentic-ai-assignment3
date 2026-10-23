"""read_issue: return one issue from the tracker. Untrusted content: anyone can file an issue.

Its definition, the only thing the model sees, is read_issue.json; the model calls it
tracker_read_issue.
"""

MAX_BODY = 8_000   # a longer body comes back cut, with a note saying so


def no_such_issue(ctx, number) -> str:
    """The repair fact for an issue number the tracker does not have: the ones it has.
    The three tracker tools share it."""
    try:
        known = sorted(set(ctx.tracker.issue_numbers("eval")) | set(ctx.tracker.issue_numbers("dev")))
    except Exception:
        known = []
    have = f" The tracker has issues {', '.join(map(str, known))}." if known else ""
    return (f"issue-{number} does not exist. Use the number of the issue you were asked to triage.{have} "
            f"Retrying {number} will fail.")


def run(ctx, issue_number):
    from harness.tracker import IssueNotFound
    from tools import ToolError

    try:
        issue = ctx.tracker.issue(int(issue_number))
    except IssueNotFound:
        raise ToolError(no_such_issue(ctx, issue_number))
    body = issue["body"] or "(no description)"
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY] + f"\n[truncated at {MAX_BODY:,} of {len(body):,} characters]"
    return (f"issue-{issue['number']}: {issue['title']}\n"
            f"Opened by {issue['author']} on {issue['opened']}\n\n"
            f"{body}")

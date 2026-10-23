"""post_comment: write a comment on an issue. Public: whoever filed the issue can read it.

Its definition is post_comment.json; the model calls it tracker_post_comment. `idempotency_key` is not in the definition:
the model never sees or sets it. Your registry passes it (Assignment 3, Part C,
task 2), and the tracker accepts each key once.
"""

MAX_BODY = 4_000


def run(ctx, issue_number, body, idempotency_key=None):
    from harness.tracker import IssueNotFound
    from tools import ToolError
    from tools.read_issue import no_such_issue

    if not str(body).strip():
        raise ToolError("body is empty: write the triage comment, starting \"label: ...\" then \"file: ...\". "
                        "Nothing was posted.")
    if len(str(body)) > MAX_BODY:
        raise ToolError(f"body is {len(str(body)):,} characters; shorten it to at most {MAX_BODY:,}. "
                        f"Nothing was posted.")
    try:
        comment = ctx.tracker.post_comment(ctx.run_id, int(issue_number), str(body), key=idempotency_key)
    except IssueNotFound:
        raise ToolError(no_such_issue(ctx, issue_number) + " Nothing was posted.")
    return f"Posted comment-{comment['id']} on issue-{issue_number}."

"""open_pr: open a draft pull request on the mock remote. New in Assignment 3.

Its definition is open_pr.json; the model calls it pulls_open_draft. The mock remote needs no commits: a PR is a branch,
a title and a body, stored in the tracker's database. `idempotency_key` is not in
the definition: your registry passes it, as for post_comment.
"""

MAX_TITLE = 120
MAX_BODY = 4_000


def run(ctx, issue_number, branch, title, body, idempotency_key=None):
    from harness.tracker import IssueNotFound
    from tools import ToolError
    from tools.read_issue import no_such_issue

    if len(str(title)) > MAX_TITLE:
        raise ToolError(f"title is {len(str(title))} characters; shorten it to at most {MAX_TITLE}. "
                        f"Nothing was opened.")
    if len(str(body)) > MAX_BODY:
        raise ToolError(f"body is {len(str(body)):,} characters; shorten it to at most {MAX_BODY:,}. "
                        f"Nothing was opened.")
    try:
        pr = ctx.tracker.open_pr(ctx.run_id, int(issue_number), str(branch), str(title), str(body),
                                 key=idempotency_key)
    except IssueNotFound:
        raise ToolError(no_such_issue(ctx, issue_number) + " Nothing was opened.")
    return f"Opened draft pr-{pr['id']} from {branch} for issue-{issue_number}."

# Predictions

## Duplicate comments

- **My harness: 0.** Every comment has a fixed key, so the tracker keeps only one
  however often it is sent.
- **Control: about 9, in about 6 of its 12 runs.** When a comment posts but the tracker
  says "timeout", the control retries with a new random key, so the tracker posts it
  again.

## Duplicate PRs

- **My harness: 0.** Before every attempt it checks for an open PR on the branch
  `repomind/issue-<n>`.
- **Control: 0.** It does the same branch check, so it finds the PR it already opened.

## The tool the model will misuse most

- **`code_search`.** In the dev runs the model searched for a vague word ("Export", 53
  files) and picked the wrong file instead of narrowing the search.

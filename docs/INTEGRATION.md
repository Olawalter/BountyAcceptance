# Integration

For a contract or service that acts on a bounty's outcome, a sponsor who posts
one, and a developer who answers one.

## The consumer's interface

```python
import genlayer as gl

@gl.contract_interface
class BountyBoard:
    class View:
        def get_outcome(self, bounty_id: str) -> dict: ...
        def get_bounty(self, bounty_id: str) -> dict: ...
        def get_submission(self, submission_id: str) -> dict: ...
        def get_balance(self, wallet: str) -> dict: ...
```

Amounts are strings of atto. Times are ISO-8601 UTC, like
`2026-10-05T12:00:00Z`. A view has no clock: the two that answer "what may
happen now" take the moment as `as_of`.

## What each view answers

| View | Answers |
|---|---|
| `get_outcome` | one bounty, for a machine: `released` and `returned`, `winner`, `winning_submission`, `closed_by`, the reward and the bounty's hash |
| `get_bounty` | the frozen bounty verbatim with its hash, the reward and bond, its status, and the submissions still open |
| `get_submission` | the pinned documents and the endpoint, the commitment, the bond, the verdict and each requirement's state, whether it is in doubt, and how it ended: `result`, `result_reason`, `bond_fate` |
| `get_evaluation` | one round's record: what was read, whether each endpoint call was answered, each finding with its quoted passages, the outcome, whether it was applied |
| `get_history` | a submission's rounds, in order |
| `get_actions` | what may happen to a submission at `as_of` and who may do it; `waits_for` names an earlier submission that is first in line |
| `get_bounty_actions` | whether a bounty may be funded, cancelled, submitted to or closed at `as_of` |
| `get_balance` | what the contract owes a wallet: what `withdraw` would pay |
| `list_bounties` / `list_submissions` | ids, paged by `offset` and `limit` (at most fifty) |
| `get_stats` / `get_config` | counts and custody; every vocabulary, limit and marker phrase the contract uses |

## Reading an outcome correctly

- **`released` is the fact to act on.** It is true only after an accepted
  submission was finalized and paid. An `ACCEPTED` verdict on a submission is
  not yet that: it can be contested, and an earlier submission can be first.
- **`open` means funded and not closed**, not "still taking submissions": the
  deadline may have passed while a submission is being decided. Use
  `get_bounty_actions` for `may_submit`.
- **A bounty is identified by its hash as well as its id.** If you agreed to
  requirements off-chain, compare `bounty_hash`.
- **`NOT_READ` and `OUTRUN` say nothing about the work.**
- **`closed_by` says why a reward went back**: cancelled before any submission,
  or nothing accepted by the deadline.

## Following a submission

Poll `get_actions(submission_id, now)`:

- `may_evaluate` with `evaluate_by` - who may ask for a round; `open_round`
  says what the last one could not do;
- `may_contest` - which party may still contest;
- `in_doubt` and `may_restore` - the sponsor's contest could not reach the
  deliverable and the developer may show it is back;
- `may_finalize`, or `waits_for` when an earlier submission holds its place;
- `may_lapse` - nothing read it in time.

Each round appears in `get_history`, and `get_evaluation` says which
requirement a rejection turned on.

## Writing, for a sponsor

`create_bounty(bounty_json)` returns the bounty id; `fund(bounty_id)` carries
exactly the reward. [DECISION.md](../DECISION.md) has a complete example. In
short:

- one to eight requirements; one to six fields, at most four documents and two
  endpoints; every field a check reads must be required;
- `reward` and `bond` in atto; the bond is at most the reward and may be 0;
- `document_hosts` lists where a document may be hosted - hosts that serve
  immutable, pinned content; `endpoint_hosts` lists where a service may run,
  and an empty list means any public host;
- `submit_by` is at least an hour after funding; `evaluate_window` at least an
  hour; `contest_window` at least fifteen minutes.

A refused `fund` returns its value in the same call and says why in its
result: read the result, do not assume.

## Writing requirements that hold

- Put everything code can decide in a `CHECK`. It is exact and cannot be
  argued with.
- Ask an endpoint for what a real service has and a placeholder does not: a
  route that answers 200 with a named key, and a route that answers 404.
- Remember what a check cannot show. Static files answer `GET`. If behaviour
  matters, ask for evidence of it as a document and judge that.
- Write a `JUDGED` requirement as one statement the documents either show or
  do not: "the documentation explains how a client authenticates", not "the
  documentation is good".
- Every requirement must be met. Leave out what you would accept without.
- Never put a secret in a path. A bounty is public.

## Writing, for a developer

`submit(bounty_id, submission_json)` with exactly the bond attached:

```json
{"fields": {"api": "https://service.example.net/base",
            "docs": {"sha256": "<64 hex>", "urls": ["https://..."]}}}
```

- Pin the documents first, then submit their digests. They cannot be improved
  afterwards; `add_mirror` only adds another address for the same bytes.
- Have the service running before you submit, and ask for your own first
  round at once. Anyone may ask for it, at a moment of their choosing; a
  deliverable that is called and is not there has fifteen minutes to be asked
  for again before it loses its place in line, and its bond if another
  submission is paid meanwhile.
- You have two submissions per bounty, one at a time, and one contest of a
  rejection that is read.
- If the sponsor contests your acceptance while your service is down, the
  acceptance is in doubt: bring it back and call `restore` inside the window.
  You have three rounds for each doubt.
- A refused `submit` returns the bond in the same call and says why.
- What you are owed - a reward, a bond that came back - waits in your balance
  until you call `withdraw`.

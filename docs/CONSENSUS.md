# Consensus

What happens inside one evaluation round, what nodes have to agree on, and
what a leader acting alone cannot get past them. The code is `_node_round`,
`_parse_payload`, `_evidence_difference`, `_derive` and `_validator_decision`
in `contracts/bounty_acceptance.py`.

## The exact nondeterministic calls

Three methods run a round - `evaluate`, `contest` and `restore` - and each is
a single `gl.vm.run_nondet_unsafe(leader_fn, validator_fn)`. Inside it, a node
makes:

| Call | For | How often |
|---|---|---|
| `gl.nondet.web.get(url)` | each pinned document, trying its addresses in order until one serves the pinned bytes | at most three addresses for each of at most four documents |
| `gl.nondet.web.get(base + path)` | each endpoint check, at the developer's base and the bounty's path | once per distinct URL; not at all once a document has proved unreadable |
| `gl.nondet.exec_prompt(..., response_format="json")` | the `JUDGED` requirements, in one prompt | once, and only when code has not already settled them |

No other method, and no view, leaves deterministic code.

## What every node does

1. **Documents.** Fetches each one and compares the sha256 of the bytes with
   the pin. If no address serves the pinned bytes for any document, the round
   is `DELIVERABLE_UNAVAILABLE` with reason `EVIDENCE_UNREADABLE`.
2. **Text.** Turns bytes into text by fixed rule: strict UTF-8; for an HTML
   document the markup is dropped, with a gap where a block element stood and
   none where an inline one did.
3. **Endpoint.** Calls `GET` on every URL the bounty's endpoint checks need.
   An answer is any status below 500 other than 408, 425 and 429. No answer,
   a server error, or one of those three makes the round
   `DELIVERABLE_UNAVAILABLE` with reason `ENDPOINT_UNREACHABLE`: a service
   that is down or busy has not been shown to lack a route.
4. **Checks.** Decides every `CHECK` from the document text and from what the
   endpoint answered: the exact status, an exact string in the body, JSON of a
   shape, a top-level key.
5. **Markers.** Scans the documents for the published phrases that address the
   evaluator. If one is found, or no document has any text, every `JUDGED`
   requirement is NOT_MET by code and no model is called.
6. **Panel.** Otherwise shows the model the requirements and the document
   text - never the endpoint or its answers - and asks MET or NOT_MET for each
   judged requirement, with a quoted passage for a MET.
7. **Grounding.** Every quote must be one contiguous passage of the text this
   node showed. A MET with nothing grounded becomes NOT_MET. No usable state
   for any judged requirement makes the round `PANEL_UNUSABLE`.
8. **Verdict.** `ACCEPTED` if every requirement is MET, `REJECTED` otherwise.

## What the leader does

Runs those steps and returns one payload: for each document what it could do
with it and what it read; for each endpoint call whether it was answered; the
markers; one finding per requirement.

## What the validator does

Runs the same steps from scratch - its own fetches, its own calls to the
endpoint, its own model call - and then, in order:

1. **Gate.** The leader's payload must have exactly the expected shape and name
   this submission, this round and this bounty's hash. Every `CHECK` finding
   is recomputed from what *this validator* fetched and was answered, and must
   equal the leader's. Every quote must be found in the text this validator
   read. A status the payload gives for a call may not contradict the state it
   gives the requirement that made the call.
2. **Evidence.** The reason the round was or was not read; the markers;
   whether each call was answered; for each document whether it was readable,
   its size, the digest of its bytes, the digest of its text, its title.
3. **Consequence.** The state of every requirement, and the verdict.

Anything that differs is a disagreement, and the validator prints which.

## What must match, what may differ

| Must match | May differ |
|---|---|
| whether each document was readable | which address served it |
| size, byte digest, text digest and title of each document read | the status and content type it came with |
| whether each endpoint call was answered | the status and size of the answer, within what the states allow |
| the marker phrases found | the wording of a finding's note |
| MET or NOT_MET for every requirement | which passage was quoted for a MET (each must be grounded) |
| the verdict | |
| for a round that decides nothing: that it does, and why | which document or call failed |

The body of an endpoint's answer is not compared. A live service may put a
timestamp in it; what nodes must agree on is what the answer means for each
requirement, and each computes that from the answer it got. Everything in the
right-hand column is listed in each stored record under `leader_chosen`.

## Decision-critical fields

The requirement states. The verdict is a function of them and of nothing else,
there are at most eight, each has two values, and every one is compared.
Nothing is scored, so there is no tolerance to set.

## Forged-leader defence

| A leader that | Is refused because |
|---|---|
| reports a judged requirement MET that the documents do not support | the validator's own reading says NOT_MET |
| quotes a passage that is not in a document, or stitches one from fragments | the gate cannot ground it; a quote with an ellipsis is refused outright |
| reports an endpoint check MET that the service does not meet | the validator recomputes it from the answer the service gave *it* |
| says a call was answered when it was not, or the reverse | the validator's own call differs |
| says the deliverable was unreachable when it was there | the reason differs |
| passes a judged requirement off as code's, or a check as the panel's | each finding must name who decided it |
| returns a payload for another submission, round or bounty | the ids, the round number, the bounty hash and the commitment are checked |
| fails with an invented error | an error is ratified only by the same deterministic error, or a transient one by a transient one |

What a majority of honest validators cannot prevent is a service that answers
most of them well and a few badly: the result that is stored is the one the
leader and a majority each saw for themselves.

## Failure semantics

A round that decides nothing still reaches consensus - on the fact that
nothing could be decided, and on why. It is recorded with `applied` false,
stores only the status of each source and no markers, and changes no verdict.
What it does change is narrow and deliberate:

- after `evaluate`, a deliverable that was asked for and was not there keeps
  its place in line for fifteen minutes and then holds none until a round reads
  it, and only its developer may ask again;
- after the sponsor's `contest`, an acceptance whose deliverable could not be
  reached is in doubt until the developer's `restore` reaches it.

If consensus itself is not reached the transaction does not execute and
nothing is recorded; the call can be sent again.

## Why consensus is load-bearing

Take the validators away and the leader's reading releases the escrow. Take the
model away and no `JUDGED` requirement can be decided. What would still run is
the deterministic part - and for the endpoint checks even that needs more than
one node, because what a live service answers is only a fact once several
callers have seen it.

## Live findings

The run of record made 15 consensus rounds. Every one finalized with the
leader's result accepted; no validator disagreed in this run, so the
forged-leader paths are shown by the Direct Mode suite - which replays a
validator against forged payloads - and not by the live run. What the live
network did show:

- **Validators call a developer's endpoint from inside a round.** Endpoint
  checks were decided both ways on chain: a status answer with the key and one
  without it, a route that answers 200 and one that answers 404.
- **A host that does not exist is "no answer", not a failed check.** The round
  on a deliverable whose service was not there reached consensus on
  `ENDPOINT_UNREACHABLE` and changed nothing.
- **A document can disappear between two rounds, and the contract notices.**
  The same pinned document was read, then was gone when the sponsor contested,
  then was read again when its developer brought it back.
- **What a validator can fetch is not what an observer can.** An earlier run
  took the documents down and contested as soon as this machine saw them gone;
  the validators' route to the host still served them from its cache, and the
  contests read them. The run now waits out the cache before relying on a
  document being gone or back.

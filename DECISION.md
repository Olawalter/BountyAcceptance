# Decision record - BountyAcceptance

Written before the contract. It fixes what the contract decides, what it refuses
to decide, and why each boundary sits where it does.

## The trust question

A bounty is a promise made to nobody in particular: build this, and the reward
is yours. Moving the reward is easy. The hard part is the one question both
sides will disagree about:

> **Given a bounty whose requirements were frozen and funded before anyone
> submitted, and a deliverable a developer put forward - documents pinned by
> digest and an endpoint that is live - does the deliverable satisfy every
> requirement as written?**

The contract accepts or rejects deliverables. It does not rate developers, does
not rank one submission against another, and has no opinion on whether the
bounty was worth posting.

## The delete-GenLayer test

Delete GenLayer and acceptance is whatever the sponsor says after looking, with
the reward still in the sponsor's hands. "The documentation explains how a
client authenticates and what happens when a key is revoked" is satisfied or not
by a page of documentation. That is a reading: code cannot make it, and the
sponsor's answer is the answer of the party that keeps the money if it is no.
Several independent validators can each fetch the same pinned document and call
the same live endpoint, read them against the same frozen requirement, refuse a
leader whose reading is well-formed but wrong, and release an escrow on the
result.

Everything code can decide stays in code: whether the endpoint answers a path
with the status the bounty names, whether a response is JSON with a given key,
whether a document contains a passage. GenLayer handles the contextual
judgement; it does not replace a test that code can run.

## Collision audit (the owner's own portfolio)

The nearest repositories are described by what they do, not named.

| Nearest | What it decides | Why this is different |
|---|---|---|
| a contribution-reward contract | how good a contribution was, and what share of a pool it earns | a graded judgement of quality; this is a yes or no against requirements fixed in advance, and the reward is not divided |
| a registry of specifications and what complies with them | whether a subject's pinned evidence substantiates a published specification; it issues certificates and holds no funds | a standing registry with no counterparty; here there is one reward, a queue of competing submissions, an order of priority between them, and money that moves on the answer |
| a two-party agreement contract | whether one named party delivered what two signers agreed, with a settlement split between them | a private agreement with a named counterparty; a bounty is open to anyone, the first acceptable deliverable takes all of it, and what is evaluated includes a service that is running |

## The rule that makes it distinct

**The requirements are frozen when the bounty is created, the reward is in the
contract before the first submission, and the first submission that satisfies
every requirement takes the whole reward.** "First" is the order of submission,
not the order in which anyone happened to evaluate or finalize: an accepted
submission is paid only when no earlier submission can still be accepted.

A requirement is either a `CHECK` that code decides or a `JUDGED` requirement a
panel reads. A deliverable is `ACCEPTED` only if every requirement is met. There
is no partial acceptance and no partial payment.

## Responsibility split

**Deterministic code owns:** identity; the frozen bounty and its hash; custody
of the reward and of every bond; which fields a submission must carry; URL
admission and the bounty's hosts; the sha256 pin of every document and the
digest check every round; how bytes become text; calling the live endpoint and
deciding every `CHECK`; text addressed to the evaluator; the acceptance rule;
the order of priority between submissions; windows from transaction time; who
may act and when; every transfer.

**GenLayer consensus owns meaning:** whether the pinned documents show that a
`JUDGED` requirement is satisfied.

**The model never accepts or rejects.** It returns, per judged requirement, MET
or NOT_MET, with the passage that shows a MET. Code derives the verdict and
moves the money.

## The bounty (immutable, hashed)

```json
{
  "title": "Rate-limit status API (test only)",
  "summary": "what the bounty is for - context, never a requirement",
  "reward": 2000000000000000000,
  "bond": 50000000000000000,
  "fields": [
    {"id": "api", "label": "Base URL of the running API", "type": "ENDPOINT", "required": true},
    {"id": "docs", "label": "Documentation", "type": "DOCUMENT", "required": true}
  ],
  "requirements": [
    {"id": "r1", "kind": "CHECK", "text": "GET /v1/status answers 200.",
     "check": {"type": "RESPONDS", "field": "api", "path": "/v1/status", "value": 200}},
    {"id": "r2", "kind": "CHECK", "text": "The status response is a JSON object with a limit.",
     "check": {"type": "RESPONSE_JSON_KEY", "field": "api", "path": "/v1/status", "value": "limit"}},
    {"id": "r3", "kind": "CHECK", "text": "An unknown route answers 404.",
     "check": {"type": "RESPONDS", "field": "api", "path": "/v1/nothing-here", "value": 404}},
    {"id": "r4", "kind": "JUDGED",
     "text": "The documentation explains how a client authenticates and what happens when a key is revoked."}
  ],
  "document_hosts": ["raw.githubusercontent.com", "cdn.jsdelivr.net"],
  "endpoint_hosts": [],
  "submit_by": "2026-11-01T00:00:00Z",
  "evaluate_window": 86400,
  "contest_window": 86400
}
```

One to eight requirements; one to six fields, at most four documents and two
endpoints.

| Field type | Holds | Checked at submission |
|---|---|---|
| `DOCUMENT` | a sha256 and one to three addresses that serve those bytes | digest format, URL admission, host on the bounty's `document_hosts` |
| `ENDPOINT` | the base URL of something that is running | URL admission, no query string, and a host on the bounty's `endpoint_hosts` if it names any - an empty list means any public host, since a sponsor cannot know in advance where a stranger's service will run |

| Check | Reads | Met when |
|---|---|---|
| `CONTAINS` / `NOT_CONTAINS` | a document | its text has / lacks a passage, word for word |
| `MIN_WORDS` | a document | its text has at least that many words |
| `VALID_JSON` | a document | the file parses as JSON (`any`, `object` or `array`) |
| `RESPONDS` | an endpoint | `GET base + path` answers with exactly the HTTP status the bounty names: a success (200 to 299) or a client error (400 to 499) |
| `RESPONSE_HAS` | an endpoint | it answers 200 and the body contains an exact string |
| `RESPONSE_JSON` | an endpoint | it answers 200 and the body parses as JSON of the named shape |
| `RESPONSE_JSON_KEY` | an endpoint | it answers 200 with a JSON object that has the named top-level key |

A bounty may not name a redirect (whether a node sees one or follows it is the
network's choice, not the service's), nor 408, 425 or 429, which a healthy
service gives only for a moment. A string or key an endpoint check looks for is
matched character for character, so it is written in printable ASCII: a key
that only looks like `limit` would be one no honest service has, and a bounty
built on it would collect bonds and pay nobody.

The path is the sponsor's, frozen in the bounty; the base is the developer's.
A developer cannot choose which path is called, and a sponsor cannot choose
which server.

**Documents are pinned; an endpoint is live.** A document is evidence only as
the bytes that were pinned. An endpoint is evaluated as it answers when a round
calls it - that is what "running" means - and each validator calls it itself.

## The acceptance rule

Every requirement ends MET or NOT_MET. The burden is the developer's: what the
deliverable does not show is not met.

| Verdict | When |
|---|---|
| `ACCEPTED` | every requirement is met |
| `REJECTED` | any requirement is not met |

## State machine

```text
create_bounty ─► CREATED ─ fund (the reward) ─► FUNDED ─┬─ a submission is accepted and final ─► RELEASED (to the developer)
                    └ cancel ─► CANCELLED               ├─ cancel, before any submission      ─► RETURNED (to the sponsor)
                                                        └─ close, after submit_by, nothing open ─► RETURNED

submit (with the bond) ─► SUBMITTED ─ evaluate ─► EVALUATED: ACCEPTED or REJECTED ─ (contest window) ─ finalize ─► FINAL
        │                     └ a round that decides nothing is recorded and changes nothing
        └ lapse, when no round read it by its deadline ─► FINAL, not read
```

| Step | Who | When |
|---|---|---|
| `create_bounty`, `fund` | the sponsor | `fund` carries exactly the reward |
| `cancel` | the sponsor | before funding, or funded with no submission ever made |
| `submit` | anyone but the sponsor, with exactly the bond | while the bounty is funded and `submit_by` has not passed |
| `add_mirror` | the developer | until its submission is final |
| `evaluate` | anyone first; after a round that could not reach the deliverable, the developer; after an unusable model answer, the developer or the sponsor | until the submission's read-by time |
| `contest` | the developer against a rejection, the sponsor against an acceptance; one read contest each | inside the contest window |
| `restore` | the developer | when the sponsor's contest could not reach the deliverable |
| `finalize` | anyone | after the contest window |
| `lapse` | anyone | after the read-by time, if no round read the submission |
| `close` | anyone | after `submit_by`, when no submission is open |
| `withdraw` | each wallet, its own balance | any time |

| Situation | Rule |
|---|---|
| a document cannot be fetched, or the endpoint does not answer | the round decides nothing and is recorded. The read-by time does not move: the developer has until then to be readable. No answer, a server error, and a 408, 425 or 429 are all "does not answer": a service that is down or busy has not been shown to lack a route |
| the model's answer cannot be used | the round decides nothing |
| no round reads a submission by its read-by time | it is final, not read, and its bond goes to the sponsor |
| the sponsor contests an acceptance and the deliverable cannot be reached | the acceptance is in doubt. A round that reaches the deliverable clears it, and the developer has up to three `restore` rounds for each doubt to get one. If that round also reads the deliverable, its reading is the verdict and the sponsor's contest has been answered; if the model gave nothing usable, the acceptance stands as it was and the sponsor may contest again if it has a contest round left. Still in doubt when the window ends: rejected |
| a document carries text addressed to the evaluator | every judged requirement is not met, without the panel; the phrases are published in `get_config` |
| an accepted submission's window has passed but an earlier submission still holds its place | `finalize` waits: the earlier one is first in line. Every place in line ends at a known time, or sooner |

## Who is first

Submissions are evaluated independently and in any order. Priority is settled
at the end: an `ACCEPTED` submission is paid only when no earlier submission
still holds its place in line. An earlier submission holds its place while:

- nobody has asked for it to be read and its read-by time has not passed, or
- a round was asked for and the model gave nothing usable, while it has a round
  left and its read-by time has not passed, or
- it stands accepted and has not been finalized - unless it is in doubt and
  either its window has passed or it has no restore round left, when it can
  only be rejected, or
- it stands rejected and its developer can still contest.

**A submission that a round asked for and could not reach holds its place for
fifteen minutes more, and then holds none.** A place in line is for a
deliverable that is there when it is called. Anyone may ask for the first round
at once, so a placeholder submitted ahead of real work is found missing as soon
as a rival cares to call. But whoever calls picks the moment, and a real
service can be caught restarting: the fifteen minutes are for its developer to
ask again. They run from the round that first found it missing; asking again
and still not being there buys no more. After them it stands in front of
nobody, and gets its place back only by a later round that reaches it: one that
accepts it, one that rejects it while its developer can still contest, or one
the model could not answer while it has a round left. A developer
who asks for its own first round as soon as it has submitted, and is read,
cannot be caught this way at all.

Every place ends at a known time, and nobody has to finalize or lapse an
earlier submission whose place has ended for a later one to be paid. When a
submission is paid, every other open submission on the bounty ends with it: one
that still held a place - it could still have been accepted - ends `OUTRUN`
and its bond is returned; one that held none ends as it was going to end - not
read, or rejected - and its bond is forfeit. A submission that had lost its
place and was not reached again does not get its bond back because somebody
else was paid.

A developer may have one open submission per bounty and two in all. A bounty
has at most twelve open submissions at a time.

## The bond

A sponsor may require a bond with every submission. It is returned when the
submission is paid or outrun, and goes to the sponsor when the submission is
rejected or is never read. It is the price of a place in the queue: without it,
twelve throwaway wallets could fill a bounty's open places for the length of a
window at no cost. A sponsor who sets it to zero accepts that. A bond cannot
exceed the reward, and it is frozen with the bounty, so a developer sees it
before deciding to submit.

The bond does not price everything. Wallets that fill the places behind a
submission that goes on to be paid, with deliverables nobody asks to read, are
outrun with it and get their bonds back: for as long as that takes, nobody else
can submit. And a sponsor's own wallets pay nothing in any case, because a
forfeited bond goes to the sponsor.

## The panel's question

For each `JUDGED` requirement, in the sponsor's frozen words: do the pinned
documents show that it is satisfied?

| State | Means | Must quote |
|---|---|---|
| `MET` | the documents show the requirement is satisfied | a passage of a document that shows it |
| `NOT_MET` | they do not show that | nothing |

The panel is shown the bounty's requirements and the text of the documents. It
is not shown what the endpoint answered: whether the service does what a
requirement says is for a `CHECK` that calls it, and whether the documents say
what a requirement asks is for the panel.

## The derivation (code, in this order)

1. every supplied document is fetched from its addresses until one serves the pinned bytes; if none does for any document, the round is `DELIVERABLE_UNAVAILABLE` and decides nothing
2. every endpoint check calls `GET base + path`, once per distinct URL; a call that gets no answer, a 5xx one, or a 408, 425 or 429 makes the round `DELIVERABLE_UNAVAILABLE`
3. every `CHECK` requirement is decided in code
4. a document carries text addressed to the evaluator, or no document has any text -> every `JUDGED` requirement is `NOT_MET`, without the panel
5. otherwise the panel reads the `JUDGED` requirements; an answer unusable for any one of them makes the round `PANEL_UNUSABLE`, which decides nothing
6. `ACCEPTED` if every requirement is met, `REJECTED` otherwise

## What validators compare

What each node could do with each document; for each one read, its size, the
digest of its text and its title; whether each endpoint call was answered; the
markers; the state of every requirement; the verdict. Every `CHECK` is
recomputed by each validator from what it fetched and what the endpoint
answered to it. For a round that decides nothing, nodes agree on that and on
why. Notes, the choice of passage, how a fetch failed, which address answered,
the status and content type a document came with, and the status and size of
an endpoint's answer are the leader's own account and are listed as such in
every record. A record is still refused if the status it gives for a call
contradicts the state it gives the requirement that made the call.

## Custody

The contract holds each funded reward and each bond until it is decided, then
credits a balance that its owner withdraws. At every moment the contract's
balance equals what it holds in escrow plus the balances it owes; `get_stats`
reports both. A payable call that is refused returns the value it carried in
the same call and says why - it never raises with money attached. Value sent
with a call the network itself cannot dispatch - a method that does not exist,
the wrong number of arguments - never reaches this code; what the contract
guarantees is that its balance is never less than what it reports.

## Three consumers

| Consumer | Reads |
|---|---|
| a bounty board or grants desk that lists open work | `list_bounties`, `get_bounty`: the frozen requirements, the reward, the bond, the deadline |
| a DAO treasury or another contract that must act when work is accepted | `get_outcome`: whether the bounty was released, to whom, for which submission |
| an agent that delivers work for bounties | `get_actions` and `get_evaluation`: what it may do next, and which requirement a rejection turned on |

## Known limits

- **The deliverable is what the developer pins and what its endpoint answers.**
  The contract says whether that satisfies the requirements. It does not know
  who wrote it, whether it is original, or whether the endpoint will still
  answer tomorrow.
- **An endpoint is live, so a place in the queue can be taken before the work
  is done.** A developer can submit early and finish the service before a
  round reads it, or fix it and contest a rejection. Three things bound this:
  documents are pinned at submission and cannot be improved; anyone may ask for
  the first round at once, and a placeholder that is called holds its place
  for fifteen minutes if it is found missing, and through at most two contest
  windows if it is read and rejected; and a developer has two submissions and
  one read contest. A placeholder nobody asks about
  keeps its place until its read-by time, and so does one whose host answers
  validators so unevenly that no round about it reaches consensus.
- **An endpoint can answer validators differently.** Each validator calls it
  itself and recomputes every check from what it was answered. A result is
  stored only if the leader and a majority saw answers that lead to the same
  states: a service that answers most of them well is accepted even if a
  minority saw it fail, and one that answers only a minority well is not. The
  sponsor's contest is a second sample.
- **An endpoint check does not prove a service.** A handful of static files
  served from the right paths answers `GET` like a small read-only service
  does. A sponsor who needs behaviour shown must ask for it in a way a file
  cannot fake, or as a judged requirement on pinned evidence.
- **A place can be lost to a bad moment.** A service that is down for more
  than fifteen minutes after somebody asks for it loses its place, and its
  bond if another submission is paid meanwhile. The remedy is to be running
  before submitting and to ask for the first round oneself.
- **URL hygiene is not a firewall.** The contract refuses addresses that are
  plainly not public names - IP literals, local and internal suffixes, other
  ports - but a name can resolve anywhere. What a validator may reach is for
  the validators' network to enforce.
- **Redirects are the network's.** If a node's fetch follows a redirect, an
  endpoint on an allowed host can be answered from another one, and
  `endpoint_hosts` does not stop that. Documents are not affected: their bytes
  are pinned.
- **A status is taken at its word.** A 403 from a bot wall or a 404 during a
  deploy is an answer, and a requirement that asked for 200 is not met by it.
  Only no answer, a server error and 408, 425 or 429 leave a round undecided.
- **A sponsor's contest lands when it lands.** If the deliverable cannot be
  reached at that moment the acceptance is in doubt, and the developer has the
  contest window - at least fifteen minutes, as long as the sponsor set it -
  and three rounds to show it is back. A sponsor who can make a service
  unreachable has a reason to try then - and so has a rival, when the service
  is first called.
- **An unusable answer is nobody's fault, and it keeps a place.** A submission
  whose round reached it and got nothing usable from the model holds its place
  until its read-by time while it has a round left, even if a check had
  already failed. The developer wrote the document the model is reading. The
  developer or the sponsor may ask again - a rival may not - and four rounds
  is all it has. All told, a submission that is never accepted can stand in
  front of others until its read-by time and, if it is read and rejected at the
  last moment, through two contest windows more.
- **An endpoint check is a GET and a look at the answer.** It shows that a
  route exists and what it returns to an anonymous caller once. It is not a
  test suite, a load test or a security review.
- **A developer who operates the host** can stop any round reading its
  deliverable. The result is a submission that is never read, never one that is
  accepted.
- **A sponsor can enter its own bounty from another wallet.** If its deliverable
  is first and passes, it takes its own reward back, and a bond it forfeits
  comes back to it. The contract cannot tell a sponsor's second wallet from a
  stranger; what it guarantees is that a deliverable submitted earlier,
  reachable when it is called, and satisfying the requirements is paid first.
- **A sponsor can contest every acceptance once.** A contest is another reading
  by consensus, not a veto.
- **The bond of an honest developer whose work is rejected goes to the
  sponsor.** It is frozen with the bounty and visible before submitting.
- **The marker phrases and the lookalike-letter table are tripwires**, not
  complete defences. What stands against planted instructions is that
  documents are given to the model as data, that MET must quote a passage that
  is there, and that validators read independently.
- **The text of a document is fixed by rule, not by a browser.** Markup is
  removed for a full HTML document; text a stylesheet hides is read; text a
  stylesheet adds is not; what sits inside a form control is not read. A
  fragment that does not begin as an HTML document is read as plain text,
  markup and comments included.
- **Time is the transaction's.** Every window is measured against the
  transaction time the network supplies, read as UTC.

## Deliberately left out

- **Partial payment, splitting a reward, several winners.**
- **Rating developers or ranking submissions.**
- **Changing a bounty after it is created.** A sponsor who wants different
  requirements posts another bounty.
- **Authenticated or non-GET calls to an endpoint.** A secret in a bounty is a
  secret given away.
- **A veto.** Neither party can overrule a reading.
- **A frontend.** The contract is the product.

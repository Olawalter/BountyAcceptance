# Security, privacy and the financial boundary

## What this contract is not

It is not a code review, a security audit, a test suite or a legal agreement.
It does not check who wrote a deliverable, whether it is original, or whether
it will keep running. It holds value on a test network and has had no
third-party audit: treat it as a demonstration of a mechanism, not as custody.

It decides one thing: whether a deliverable - the documents pinned to a
submission and what its endpoint answers when called - satisfies every
requirement of a bounty that was frozen before the submission existed.

## What an acceptance means - and does not

`ACCEPTED` means every requirement was MET when a round read the deliverable:
by code for a `CHECK`, by validator consensus for a `JUDGED` requirement.
`PAID` means that acceptance became final and no earlier submission stood in
front of it. Neither means the work is good, safe, or the developer's own.
`NOT_READ` is not a finding about the work at all.

## Assets

| Asset | Protected by |
|---|---|
| a bounty's requirements | stored verbatim and hashed at creation; no method rewrites them |
| the reward | held from `fund` until one of three things: paid to the developer of the first accepted submission, returned on `cancel` before any submission, returned on `close` after the deadline |
| a bond | held from `submit` until the submission is final; returned or forfeited by a fixed rule |
| a deliverable's documents | pinned by sha256; every round re-checks the bytes |
| a verdict | derived in code from compared requirement states; changed only by a later round that read the deliverable |
| a place in line | fixed by the order of submission; lost only by not being there when called and for fifteen minutes after, by rejection, or by time |
| a balance | credited by code, paid only to its own wallet by `withdraw` |

The invariant: the contract's balance equals `escrow_held` plus
`balances_owed`, both reported by `get_stats`. The Direct Mode suite checks it
after every money-moving step and the live run checks it on chain.

## Actors

| Actor | Can | Cannot |
|---|---|---|
| sponsor | create and fund a bounty; cancel before any submission; contest an acceptance once; retry a round the model failed | change a bounty; take the reward back once somebody has submitted, except by the deadline passing with nothing accepted; submit to its own bounty from the same wallet; overrule a reading |
| developer | submit twice per bounty, one at a time; add addresses for the same bytes; contest a rejection once; restore a deliverable put in doubt | change what was pinned; be paid in front of an earlier submission that still holds its place |
| anyone | ask for a submission's first round; finalize, lapse and close when their time has come | retry a round on a deliverable that was not there; contest; restore; move anyone's balance |
| leader | propose a round's result | have it stored without validators reproducing it |
| validator | refuse a result | change one |

## Trust assumptions

- A majority of validators run the round honestly.
- A document host serves what was pinned or serves nothing; it cannot serve
  other bytes unnoticed.
- An endpoint answers honest validators alike. One that answers most of them
  well is accepted on what they saw.
- The network's fetch reaches public hosts and its redirect behaviour is its
  own; the URL hygiene in the contract is defence in depth, not a firewall.
- Transaction time is the network's, read as UTC.

## Input attacks

| Attack | Defence |
|---|---|
| instructions to the model planted in a document | documents are given as data; the published marker phrases make every judged requirement NOT_MET without the model; a MET must quote a grounded passage; validators read independently |
| a bounty that only looks satisfiable - a key or string spelled with lookalike letters, to collect bonds | what an endpoint check looks for must be printable ASCII; checks code can see contradict each other are refused at creation |
| hidden or zero-width characters, lookalike letters in documents | removed or folded before text is compared |
| markup that splits or joins words | only block elements separate text |
| a quote stitched from fragments | refused |
| a submission ahead of real work with nothing behind it | anyone may ask for its first round; called and not there, it holds its place fifteen minutes more and then none; called and failing, it is rejected |
| filling a bounty's open places | a bond per submission, forfeited when the place came to nothing; twelve places, two submissions per developer |
| a path that reaches somewhere else on the developer's host | the path is the sponsor's, frozen, and limited to letters, digits and `/ - _ . ~` with no dot-segments or query |
| an endpoint URL aimed at something internal | https only, no credentials, no port but 443, no IP literal or host that a parser could read as one, no local or internal names |
| an oversized answer or document | read up to a cap; a check on a body over the cap is not met |
| a payable call refused with value attached | the value is sent back in the same call; the call does not raise |
| a secret in a bounty | paths carry no query string; everything in a bounty is public |

## Privacy and data minimisation

Everything written to the contract is public and permanent: the bounty, every
URL, every digest, the passages quoted for a MET. Do not submit personal data,
credentials or anything confidential, and do not point a bounty at a route
that needs a secret. The contract stores digests, sizes, titles and short
quotes; it does not store documents or what an endpoint answered.

## Fail-closed policy

When the contract cannot read, it does not guess, and it never pays on a guess.
An unreadable document, a silent endpoint and an unusable model answer each
produce a round that changes no verdict. A submission nothing read in time ends
not read. An acceptance that could not be reached when it was questioned, and
was not shown to be back, ends rejected. The only path to `PAID` is a round
that read the whole deliverable and found every requirement met, followed by a
contest window, followed by the order of submission.

## Limitations

- An endpoint check does not prove a service: static files at the right paths
  answer `GET` the same way.
- An endpoint is live: a place in line can be taken before the work is done,
  within the bounds DECISION.md lists.
- A status is taken at its word: a 403 from a bot wall is an answer.
- A sponsor can enter its own bounty from another wallet, pays no real bond
  when it does, and can contest every acceptance once.
- A sponsor who can make a service unreachable has a reason to try during its
  own contest; the developer then has the contest window and three rounds.
- The bond of an honest developer whose work is rejected goes to the sponsor.
- Value sent with a call the network cannot dispatch never reaches this code.
- The marker phrases and the lookalike table are tripwires.
- StudioNet is a development network; the deployment uses synthetic data.

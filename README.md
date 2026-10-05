<p align="center">
  <img src="docs/assets/bounty-mark.svg" width="96" height="96" alt="BountyAcceptance">
</p>

<h1 align="center">BountyAcceptance</h1>

<p align="center">An open bounty that pays the first deliverable to satisfy requirements frozen in advance.<br>
A standalone GenLayer Intelligent Contract. No frontend, no token.</p>

## Thesis

Moving a bounty's reward is the easy part. The hard part is the question both
sides will answer differently: did the delivered work satisfy what was asked?
This contract freezes the requirements and holds the reward before anyone
submits, lets any developer put a deliverable forward - pinned documents and a
service that is running - and has independent validators decide, requirement
by requirement, whether it satisfies them. Code decides everything code can
decide, including calling the live service. The first deliverable that
satisfies every requirement is paid; if none does by the deadline, the reward
goes back.

## Canonical deployment

| | |
|---|---|
| Network | GenLayer StudioNet, chain id 61999 |
| Contract | [`0x5F33f983553e688325639696b89eD28f9e8a57d3`](https://explorer-studio.genlayer.com/address/0x5F33f983553e688325639696b89eD28f9e8a57d3) |
| Explorer | `https://explorer-studio.genlayer.com/address/0x5F33f983553e688325639696b89eD28f9e8a57d3` |
| Deployment tx | [`0x534c791d47c2ddc87748a88097d5e3e69fee08b889652bc0a8ca48ce261624e6`](https://explorer-studio.genlayer.com/tx/0x534c791d47c2ddc87748a88097d5e3e69fee08b889652bc0a8ca48ce261624e6) |
| Finality / status | FINALIZED, leader execution SUCCESS |
| Consensus result | AGREE x5 |
| Deployment source commit | `57ee983` |
| Current source parity | deployed source read back with `gen_getContractCode`: **byte-identical** to `contracts/bounty_acceptance.py` (sha256 `fb89654588e43607c268680702c0fdf00ec55476ab2d69a5a5fd6ceaacfb377c`) |

The full record is [deploy/deployment.json](deploy/deployment.json); how it was
produced is [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## The question

> Given a bounty whose requirements were frozen and funded before anyone
> submitted, and a deliverable a developer put forward, does the deliverable
> satisfy every requirement as written?

The contract accepts or rejects deliverables. It does not rate developers,
rank submissions, or pay in part.

## The problem

A bounty is a promise to nobody in particular: build this and the reward is
yours. Today the sponsor decides whether the promise was kept, with the reward
still in its hands - the party that keeps the money if the answer is no. A
developer cannot know in advance how "supports X, Y and Z" will be read, and
has no recourse when it is read against them.

Part of any such list is mechanical - does the route answer, is the response
JSON with this key - and part is not: *the documentation explains how a client
authenticates and what happens when a key is revoked* is satisfied or not by a
page of prose.

## Why GenLayer

The mechanical part needs no judgement and gets none: it runs as code inside
the contract, each validator calling the service itself. The other part is a
reading, and a reading by the sponsor is the reading of an interested party.
On GenLayer several validators each fetch the same pinned document, read it
against the same frozen requirement with their own model, and refuse a leader
whose answer is well-formed but not what the document supports. The result is
not an opinion: it releases an escrow.

## Delete GenLayer: what breaks?

| Without | What is left |
|---|---|
| the model | a script that can call a URL and find a string, and cannot say whether a page explains what a requirement asks |
| the validators | one model's reading, with a reward riding on it, chosen by whoever ran it |
| the contract | a bounty post, a pull request and a sponsor's say-so |

## Why this is not a rejected pattern

- **Not an oracle.** No feed is relayed. The only outside facts code fetches
  are the developer's own deliverable: its pinned documents and what its
  endpoint answers.
- **Not a rubber stamp.** Validators do not check the leader's formatting; each
  re-runs the round from its own fetches and calls and compares what was read
  and what it leads to.
- **Not a score.** There is no number to drift between models. A requirement
  is MET or NOT_MET, and a MET by the panel must quote a passage that is in the
  pinned text.
- **Not a model moving money.** The model never accepts or rejects. The
  verdict is derived in code, and code makes every transfer.
- **Not deterministic work dressed up.** Every requirement code can decide is a
  `CHECK` and never reaches the panel.

## The rule that makes it this contract

**The requirements are frozen when the bounty is created, the reward is in the
contract before the first submission, and the first submission that satisfies
every requirement takes the whole reward.** "First" is the order of
submission, not the order in which anyone evaluated or finalized.

## Requirements

A requirement is `JUDGED` (prose, read by the panel from the pinned documents)
or `CHECK` (decided in code). A bounty has one to eight; a deliverable is
`ACCEPTED` only if every one is met.

| Check | Reads | Met when |
|---|---|---|
| `CONTAINS` / `NOT_CONTAINS` | a document | its text has / lacks a passage, word for word |
| `MIN_WORDS` | a document | its text has at least that many words |
| `VALID_JSON` | a document | the file parses as JSON (`any`, `object` or `array`) |
| `RESPONDS` | an endpoint | `GET base + path` answers with exactly the status the bounty names |
| `RESPONSE_HAS` | an endpoint | it answers 200 and the body contains an exact string |
| `RESPONSE_JSON` | an endpoint | it answers 200 and the body is JSON of the named shape |
| `RESPONSE_JSON_KEY` | an endpoint | it answers 200 with a JSON object that has the named key |

The path is the sponsor's, frozen in the bounty; the base URL is the
developer's. Documents are pinned by sha256; an endpoint is evaluated as it
answers when a round calls it.

## What each ending pays

| A submission ends | When | Reward | Its bond |
|---|---|---|---|
| `PAID` | accepted, final, and no earlier submission holds a place in front of it | the whole reward, to the developer | returned |
| `OUTRUN` | another submission was paid while this one could still have been accepted | - | returned |
| `REJECTED` | a requirement was not met, or an acceptance was still in doubt when its window ended | - | to the sponsor |
| `NOT_READ` | no round read it by its read-by time, or another submission was paid after it was asked for and was not there, or after it had used its rounds without a usable reading | - | to the sponsor |

A bounty ends `RELEASED` (paid to a developer) or `RETURNED` (back to the
sponsor: cancelled before any submission, or nothing accepted by the deadline).

## Lifecycle and who moves it

```text
create_bounty ─► CREATED ─ fund ─► FUNDED ─┬─ an accepted submission is final ─► RELEASED
                    └ cancel               ├─ cancel, before any submission    ─► RETURNED
                                           └─ close, after the deadline        ─► RETURNED

submit ─► SUBMITTED ─ evaluate ─► EVALUATED: ACCEPTED / REJECTED ─ (contest window) ─ finalize ─► FINAL
              └ lapse, when nothing read it in time ─► FINAL
```

| Step | Who | When |
|---|---|---|
| `create_bounty`, `fund` | the sponsor | `fund` carries exactly the reward |
| `cancel` | the sponsor | before funding, or funded with no submission ever made |
| `submit` | anyone but the sponsor, with exactly the bond | while the bounty is funded and its deadline has not passed |
| `add_mirror` | the developer | until its submission is final |
| `evaluate` | anyone first; then the developer, or the sponsor too after an unusable model answer | until the submission's read-by time |
| `contest` | the developer against a rejection, the sponsor against an acceptance; one read contest each | inside the contest window |
| `restore` | the developer | when the sponsor's contest could not reach the deliverable |
| `finalize` | anyone | after the contest window |
| `lapse` | anyone | after the read-by time, if no round read the submission |
| `close` | anyone | after the deadline, when no submission is open |
| `withdraw` | each wallet, its own balance | any time |

## Contract surface

Twelve writes: `create_bounty`, `fund`, `cancel`, `close`, `submit`,
`add_mirror`, `evaluate`, `contest`, `restore`, `finalize`, `lapse`,
`withdraw`. Twelve views: `get_bounty`, `get_outcome`, `get_submission`,
`get_evaluation`, `get_history`, `get_actions`, `get_bounty_actions`,
`get_balance`, `list_bounties`, `list_submissions`, `get_stats`, `get_config`.
Two writes are payable: `fund` and `submit`.
[docs/INTEGRATION.md](docs/INTEGRATION.md) describes each.

## Nondeterministic operations

Three, all inside one `run_nondet_unsafe` round per `evaluate`, `contest` or
`restore`: fetching each pinned document, calling `GET` on the developer's
endpoint at each path the bounty froze, and one `exec_prompt` over the judged
requirements. Nothing else leaves deterministic code.
[docs/CONSENSUS.md](docs/CONSENSUS.md) has the detail.

## Deterministic responsibilities

Identity and permissions; the frozen bounty and its hash; custody of every
reward and bond; URL admission and the bounty's hosts; the sha256 pin of every
document, checked every round; how bytes become text; every `CHECK`, including
what an endpoint's answer means; text addressed to the evaluator; grounding
every quoted passage in the pinned text; the acceptance rule; the order of
priority between submissions; windows from transaction time; every transfer.

## Equivalence / validator design

A validator does not grade the leader's answer. It runs the same round itself -
its own fetches, its own calls to the endpoint, its own model - checks the
leader's payload against what it read and was answered (every quote must be in
its documents, every check is recomputed from its own answers), and then
compares: what each node could do with each document, the digest of each text,
whether each call was answered, the state of every requirement, and the
verdict. Any difference is a disagreement, and the reason is printed.

## Safety and failure semantics

| Situation | Rule |
|---|---|
| a document cannot be fetched, or the endpoint does not answer | the round decides nothing; it is recorded and no verdict changes |
| the model's answer is unusable for any judged requirement | the round decides nothing |
| a submission is asked for and is not there | it keeps its place for fifteen minutes, for its developer to ask again; after that it holds none until a round reads it |
| nothing reads a submission by its read-by time | it ends not read |
| the sponsor contests an acceptance and the deliverable cannot be reached | the acceptance is in doubt until the developer shows it is back; still in doubt when the window ends, it is rejected |
| a document carries text addressed to the evaluator | every judged requirement is not met, without consulting the panel |
| a payable call is refused | the value it carried goes back in the same call |

## Financial safety, privacy, and no false guarantees

The contract holds each reward and bond until it is decided and then credits a
balance its owner withdraws; at every moment its balance equals what it reports
holding plus what it reports owing. It runs on a test network with test value:
do not treat it as audited custody. Everything submitted is public and
permanent: do not submit secrets, and do not put a key in a bounty. An
acceptance says the deliverable satisfied the frozen requirements when it was
read. It does not say the work is original, secure, or still running. All data
in this repository's fixtures is synthetic and marked as test data.

## Reuse surface

| Consumer | Reads |
|---|---|
| a bounty board or grants desk that lists open work | `list_bounties`, `get_bounty`: the frozen requirements, the reward, the bond, the deadline |
| a DAO treasury or another contract that must act when work is accepted | `get_outcome`: whether the bounty was released, to whom, for which submission |
| an agent that delivers work for bounties | `get_actions` and `get_evaluation`: what it may do next, and which requirement a rejection turned on |

## Limitations

- The deliverable is what the developer pins and what its endpoint answers; the contract does not know who wrote it or whether it will still answer tomorrow.
- An endpoint check is a `GET` and a look at the answer: static files at the right paths pass it.
- An endpoint is live, so a developer can submit early and finish before a round reads it.
- A sponsor can enter its own bounty from another wallet, and can contest every acceptance once.
- A rejected developer's bond goes to the sponsor.
- The marker phrases and the lookalike-letter table are tripwires, not complete defences.
- StudioNet is a development network; the deployment is a demonstration.

The full list, with the reasoning, is in [DECISION.md](DECISION.md) and
[docs/SECURITY.md](docs/SECURITY.md).

## Verification

| Check | Result |
|---|---|
| `python -m pytest tests/direct -q` | 132 passed |
| `python scripts/mutation_check.py --jobs 4` | 339 mutations, 339 killed |
| `python -m pytest tests/integration -q` | 13 passed, reading the deployed contract |
| `python scripts/preflight.py` | every check PASS |
| `genvm-lint check contracts/bounty_acceptance.py` | ok, 24 methods |
| live run of record | 102 transactions with test value; 79 of 79 outcomes held; 33 of 33 refusals refused for their own reason; custody on chain equal to custody reported |

```bash
pip install -r requirements-test.txt
python scripts/fetch_genvm_bundle.py
python -m pytest tests/direct -q
python -m pytest tests/integration -q
python scripts/preflight.py
```

## Reviewer fast path

1. [DECISION.md](DECISION.md) - what the contract decides and refuses to decide, written first.
2. `_node_round`, `_validator_decision` and `_derive` in [contracts/bounty_acceptance.py](contracts/bounty_acceptance.py) - the round, the comparison and the acceptance rule; `finalize` and `_may_still_win` - who is paid, and who is first.
3. [docs/CONSENSUS.md](docs/CONSENSUS.md) - what must match between nodes and what may differ.
4. [deploy/live_run_transcript.json](deploy/live_run_transcript.json) - every transaction of the live run and what the chain answered.
5. `python -m pytest tests/integration -q` - reads the deployed contract and checks it against this repository.

## Licence

MIT. Copyright (c) 2026 Olawalter. See [LICENSE](LICENSE).

# Deployment

Where the contract is, how to reproduce the deployment, and what was run
against it.

## Environment

| | |
|---|---|
| Network | GenLayer StudioNet, chain id 61999 |
| RPC | `https://studio.genlayer.com/api` |
| Explorer | `https://explorer-studio.genlayer.com` |
| Runner | `py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6`, pinned in the contract's first lines |
| Toolchain | Python 3.12; versions pinned in `requirements.txt` and `requirements-test.txt` |

Keys live in `.data/`, which is gitignored. The value the live run moves is
test value from the network's faucet.

## Reproduce

```bash
pip install -r requirements-test.txt
python scripts/fetch_genvm_bundle.py
python -m pytest tests/direct -q
python scripts/deploy_studionet.py --disposable
```

`deploy_studionet.py` refuses to deploy a contract file that is not committed
and unmodified, waits for FINALIZED, requires the leader's execution result to
be SUCCESS, reads the deployed source back with `gen_getContractCode` and
compares its sha256 with the file in the tree. `--verify` re-checks the
recorded deployment; `--disposable` records under `deploy/diagnostics/`.

## Canonical deployment

| | |
|---|---|
| Contract | `0x5F33f983553e688325639696b89eD28f9e8a57d3` |
| Deployment tx | `0x534c791d47c2ddc87748a88097d5e3e69fee08b889652bc0a8ca48ce261624e6` |
| Status | FINALIZED, leader execution SUCCESS, AGREE x5 |
| Source commit | `57ee983af670898ac1d4e7f5d6d6a9f3210b3fde` |
| Source sha256 | `fb89654588e43607c268680702c0fdf00ec55476ab2d69a5a5fd6ceaacfb377c` |
| Parity | byte-identical to `contracts/bounty_acceptance.py` |
| Version | 0.1.0 |

The record is [`deploy/deployment.json`](../deploy/deployment.json).

## Verification of record

`python -m pytest tests/integration -q` reads the chain and checks that the
deployed source is this repository's, that the schema is the whole surface and
only `fund` and `submit` are payable, that the contract's balance is the
custody it reports, and that every round, ending and bounty outcome the live
run recorded is still what the chain answers. 13 passed.

## How the contract reached deployment

The contract was not deployed and then repaired. It was read adversarially
before its first deployment, by fresh readers who had not written it, and
changed until a round found nothing in the code to fix. Four rounds:

| Round | What it found | What changed |
|---|---|---|
| 1 | a placeholder submitted first, with nothing running, held its place until its read-by time and could then take the reward from an accepted later submission; a restore round that reached the deliverable but got no usable model answer left the acceptance in doubt; a sponsor could spell a key with lookalike letters so that no honest service had it, and collect bonds; redirect and transient statuses could be named; leader-told statuses could contradict the states beside them | a submission that is asked for and is not there holds no place; a deliverable that is back is out of doubt; exact values are printable ASCII; redirects, 408, 425 and 429 cannot be named and the last three are "no answer"; a record's statuses must agree with its states; endpoints got their own host list, empty meaning any public host |
| 2 | that fix let anyone cost an honest submission its place and bond by calling it at a bad moment; a host label such as `0x1` passed the IP-literal check; a rejection that could no longer be contested was outrun, bond returned, when someone else was paid; restore rounds were per submission, not per doubt | a fifteen-minute grace to ask again; a last label beginning with a digit is refused; a submission ends "outrun" only if it still held a place; each doubt has its own restore rounds |
| 3 | asking again and still not being there restarted the grace, which let a later placeholder recover a forfeit bond; an acceptance in doubt with no restore round left still held its place | the grace runs from the round that first found the deliverable missing; such an acceptance holds no place |
| 4 | nothing in the code; one sentence in the decision record promised more than the contract does | the sentence |

## Earlier deployments

All of the same source, all kept under `deploy/diagnostics/` with what was run
against them:

- a disposable rehearsal (`deployment_0xe7b6309c.json`): funding, six bounties,
  ten submissions, the evaluation rounds, both contests and the outage. 53 of
  53 outcomes held.
- a first canonical attempt (`deployment_0xf5e1002f_interrupted.json`): the
  machine driving the run was suspended for two hours part-way through, and
  the submissions' one-hour read-by times passed while it was. Nothing in it
  missed; it was incomplete.
- a second (`deployment_0x9007abc6_outage_missed.json`): the run took two
  documents down and the sponsor contested - but the validators' route to the
  host was still serving the documents from its cache, so the contests read
  them and upheld the acceptances. The contract did what it should with what
  the validators saw; the run had not waited long enough. It now waits out the
  cache's lifetime before it relies on a document being gone or back.

Each time the run of record was made again from the start on a fresh
deployment rather than patched.

## Live run of record

`scripts/live_run.py` drives the deployed contract with real transactions and
test value from thirteen wallets and records every step in
[`deploy/live_run_transcript.json`](../deploy/live_run_transcript.json): the
transaction hash, its status, the leader's execution result, the validators'
votes, and what the chain answered afterwards. The documents and the "API" - a
handful of static files that answer `GET` like a small read-only service - are
served from this repository at a pinned commit; two documents are served from
a branch so that the run can take them down and bring one back. All of it is
synthetic and marked as test data.

| | |
|---|---|
| Contract | `0x5F33f983553e688325639696b89eD28f9e8a57d3` |
| Deliverables pinned at | commit `b750e15` |
| Started / finished | 2026-10-05T15:46:17Z / 2026-10-05T17:44:41Z |
| Transactions | 102 |
| Consensus rounds | 15 |
| Outcomes checked on chain | 79 of 79 held |
| Refusals | 33 of 33 refused, each for the reason it was sent to test |
| Custody at the end | contract balance 0 = custody reported 0 |
| Missed | none |

| Path | Shown by |
|---|---|
| first in line with nothing running: read late, rejected | Q1: `REJECTED`, bond to the sponsor |
| second in line, satisfies everything; contested by the sponsor and upheld; paid only once the first could no longer be accepted | Q2: `PAID`, the whole reward and its bond |
| third in line, accepted too | Q3: `OUTRUN`, bond returned |
| the service runs, the documentation does not say what was asked; contested by the developer, rejected again | R1: `REJECTED` on the judged requirement |
| the status answer lacks the key the bounty names | R2: `REJECTED` by the check that calls it |
| documentation that addresses the evaluator | R3: judged requirement not met without the panel; checks still decided |
| nobody asks for an evaluation | R4: `NOT_READ` when its read-by time passes |
| a service that is not there | R5: the round decides nothing; `NOT_READ` |
| accepted, documentation taken down, sponsor contests, developer brings it back | D1: in doubt, restored, `PAID` |
| accepted, documentation taken down, sponsor contests, it never comes back | D2: in doubt, then `REJECTED` |
| five submissions, none accepted by the deadline | the reward returned to the sponsor |
| a bounty cancelled before funding, and one cancelled before any submission | `CANCELLED`, and `RETURNED` |
| every balance withdrawn and checked against the wallet's balance on chain | the withdraw phase |

## Mutation sweep

`scripts/mutation_check.py` breaks one rule of the contract at a time in a
scratch copy and runs the Direct Mode suite against it; a mutation the suite
does not notice is a rule nothing tests. The sweep of record is
[`deploy/mutation_sweep.txt`](../deploy/mutation_sweep.txt): 339 mutations, 339 killed.
It ran in passes, because one pass outlasts the time a single job may run
here: a later pass carries over the mutations an earlier pass of the same
contract killed. The first complete pass left forty-five survivors. Forty-four
were rules that only a second check caught - most of them in the validator's
gate, where a forged payload trips several rules at once - and each now has a
test that breaks that one rule alone (`tests/direct/test_gate.py`,
`tests/direct/test_checks.py`). One was a guard that repeats another and was
taken off the list, as one other had been earlier, each with the reason
recorded beside it.

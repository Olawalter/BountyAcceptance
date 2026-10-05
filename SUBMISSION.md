# Submission - BountyAcceptance

Copy-ready for the portal's Builder > Intelligent Contracts form. No addresses,
hashes or shell commands appear in the free-text fields: the contract goes only
in the evidence row the portal recognises as a GenLayer Explorer contract.

## Category

Bounties / work acceptance / escrow (portal: Builder > Intelligent Contracts)

## Title

BountyAcceptance - an open bounty that pays the first deliverable to satisfy requirements frozen in advance

## One-line thesis

A GenLayer Intelligent Contract that holds a bounty's reward, lets any developer submit a deliverable - pinned documents and a running service - and pays the first one that satisfies every frozen requirement: code decides what code can, including calling the service, and independent validators read the rest.

## Repository

https://github.com/Olawalter/BountyAcceptance

## Canonical StudioNet address

`0x5F33f983553e688325639696b89eD28f9e8a57d3`

## Explorer URL

https://explorer-studio.genlayer.com/address/0x5F33f983553e688325639696b89eD28f9e8a57d3

## Deployment tx

`0x534c791d47c2ddc87748a88097d5e3e69fee08b889652bc0a8ca48ce261624e6` - FINALIZED, leader execution SUCCESS

## Deployment source

commit `57ee983`, `contracts/bounty_acceptance.py`, version 0.1.0; deployed source byte-identical to that commit

## Why GenLayer is required

Whether a deliverable's documentation "explains how a client authenticates and what happens when a key is revoked" is a reading. Code cannot make it, and the sponsor's answer is the answer of the party that keeps the reward if it is no. Validators each fetch the pinned document and call the live service themselves, read against the frozen requirement, and refuse a leader the deliverable does not support.

## Consensus mechanism

One custom leader/validator round per evaluation, contest or restore. Every validator fetches the pinned bytes itself, calls the endpoint itself, recomputes every code-decided requirement from what it was answered, re-grounds every quoted passage in its own text, and compares each requirement's state and the verdict with the leader's.

## Deterministic responsibilities

Identity, the frozen hashed bounty, custody of every reward and bond, URL admission, sha256 pinning, how bytes become text, eight kinds of check including what a live endpoint answers, the acceptance rule, the order of priority between submissions, windows, every transfer.

## Failure policy

A round that cannot reach the deliverable or gets no usable model answer decides nothing and is recorded. A submission nothing reads in time ends not read. An acceptance that cannot be reached when the sponsor questions it is in doubt until the developer shows it is back. A refused payable call returns its value in the same call. Nothing is paid except on a round that read the whole deliverable.

## Reuse surface

A bounty board lists open work from the frozen requirements; a treasury or another contract acts on one outcome view when work is accepted; an agent that delivers work reads what it may do next and which requirement a rejection turned on.

## Test results

132 Direct Mode tests; 339 of 339 mutations killed; 13 integration tests reading the deployed contract; four adversarial review rounds before the first deployment, the last with nothing in the code to fix.

## Live evidence

102 transactions with test value on StudioNet from thirteen wallets: seven bounties, ten catalogue submissions through every ending, 15 consensus rounds, 79 of 79 expected outcomes, 33 of 33 refusals for their own reason, every payment checked against the wallet's balance, and the contract's balance equal to the custody it reports.

## Limitations

An endpoint check is a GET and a look at the answer, which static files can satisfy; an endpoint is live, so a place in line can be taken before the work is done; a sponsor can enter its own bounty from another wallet; planted-instruction and lookalike-letter defences are tripwires; test network, test value, synthetic data, no third-party audit.

## Portal description (986 characters, limit 1000)

BountyAcceptance is an open bounty that settles itself. A sponsor freezes the requirements and puts the reward in the contract before anyone submits. Any developer may then put a deliverable forward: documents pinned by digest and the address of a service that is running. Requirements code can decide are decided by code - the service answers a path with the status asked for, its answer is JSON with a given key, a document contains a passage - with every validator calling the service itself. Requirements that need a reading go to validators, who answer met or not met and must quote the passage a met rests on. The model never accepts or rejects: a deliverable is accepted only if every requirement is met, and the first accepted submission, in the order of submission, is paid the whole reward. If none is accepted by the deadline the reward returns to the sponsor. A round that cannot reach the deliverable decides nothing. Tested with synthetic data and test value on StudioNet.

## Evidence rows

| Type | Value |
|---|---|
| GenLayer Explorer contract | https://explorer-studio.genlayer.com/address/0x5F33f983553e688325639696b89eD28f9e8a57d3 |
| GitHub repository | https://github.com/Olawalter/BountyAcceptance |

## Reviewer fast path

Read `DECISION.md`, then `docs/CONSENSUS.md`, then `deploy/live_run_transcript.json`; run `python -m pytest tests/direct -q` and `python -m pytest tests/integration -q`.

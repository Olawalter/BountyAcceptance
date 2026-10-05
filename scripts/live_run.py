#!/usr/bin/env python3
"""Drive the deployed contract through every path with real transactions and real
value, and record what the chain answered.

    python scripts/live_run.py <address> --raw-base <pinned raw url> --phase full

Phases run in order and can be run one at a time: wallets, bounties, submit,
evaluate, contest, outage, queue, settle, lapse, close, withdraw, refusals,
custody. Every step is recorded in deploy/live_run_transcript.json under a
unique name; re-running skips steps already recorded, so a transport failure
or a rate limit never repeats work.

The documents and the "API" are served at a pinned commit from this repository,
by the raw host and by its CDN mirror. Two documents are served from a branch
instead, so that the run can take them down and bring one back: the outage
phase pushes to that branch. Every payment is checked twice - against the
balance the contract reports and against the wallet's balance on chain - and
the run ends by comparing the contract's balance with the custody it reports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import studionet_transport  # noqa: E402,F401 - retries RPC transport failures
from eth_utils import to_checksum_address  # noqa: E402
from genlayer_py import create_account, create_client  # noqa: E402
from genlayer_py.chains import studionet  # noqa: E402
from genlayer_py.types import TransactionStatus  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
KEYS = ROOT / ".data" / "demo_wallets.json"
BRANCH_CLONE = ROOT / ".data" / "evidence-branch"
TRANSCRIPT = ROOT / "deploy" / "live_run_transcript.json"
LOG = ROOT / "deploy" / "live_run.log"
RPC = "https://studio.genlayer.com/api"
WAIT = dict(interval=5000, retries=300)
PHASES = ("wallets", "bounties", "submit", "evaluate", "contest", "outage", "queue",
          "settle", "lapse", "close", "withdraw", "refusals", "custody")
SPONSOR_FUNDING = 2 * 10 ** 17    # atto the faucet gives the sponsor
DEVELOPER_FUNDING = 10 ** 16      # and each developer


def log(text: str):
    line = time.strftime("%H:%M:%S") + " " + text
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def now_iso(offset: int = 0) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + offset))


def epoch(iso: str) -> float:
    return time.mktime(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone


def wait_until(iso: str, what: str):
    target = epoch(iso)
    while True:
        left = target - time.time()
        if left <= -3:                 # past the moment, never a few seconds short of it
            return
        log("  waiting " + str(int(left) + 5) + "s for " + what)
        time.sleep(min(left + 5, 120))


# -- the transcript ------------------------------------------------------------

class Transcript:
    def __init__(self, address: str, raw_base: str, path=None):
        self.path = path or TRANSCRIPT
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.data = {"address": address, "raw_base": raw_base,
                         "started_at": now_iso(), "steps": {}, "order": []}
        if self.data["address"] != address:
            sys.exit("the transcript records a different contract; move it aside first")
        self.data["raw_base"] = raw_base

    def has(self, step: str) -> bool:
        return step in self.data["steps"]

    def done(self, step: str) -> bool:
        return self.has(step) and self.get(step).get("leader_execution") == "SUCCESS"

    def get(self, step: str) -> dict:
        return self.data["steps"][step]

    def put(self, step: str, entry: dict):
        entry["at"] = now_iso()
        entry["step"] = step
        if step not in self.data["steps"]:
            self.data["order"].append(step)
        self.data["steps"][step] = entry
        self.save()

    def save(self):
        self.data["finished_at"] = now_iso()
        self.data["summary"] = self.summary()
        self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True) + "\n",
                             encoding="utf-8", newline="\n")

    def summary(self) -> dict:
        steps = self.data["steps"].values()
        checks = [s for s in steps if "held" in s and s.get("kind") != "refusal"]
        refusals = [s for s in steps if s.get("kind") == "refusal"]
        return {
            "steps": len(self.data["order"]),
            "transactions": len([s for s in steps if s.get("tx")]),
            "outcomes_checked": len(checks),
            "outcomes_held": len([s for s in checks if s["held"]]),
            "missed": sorted(s["step"] for s in checks if not s["held"]),
            "refusals": len(refusals),
            "refusals_held": len([s for s in refusals if s.get("held")]),
        }


def _hex(value) -> str:
    return value if isinstance(value, str) else "0x" + bytes(value).hex()


def _plain(args) -> list:
    out = []
    for value in args:
        text = value if isinstance(value, (str, int, bool)) else str(value)
        if isinstance(text, str) and len(text) > 200:
            text = text[:200] + "... (" + str(len(text)) + " characters)"
        out.append(text)
    return out


def _status(receipt) -> str:
    for key in ("status", "statusName", "status_name"):
        value = receipt.get(key)
        if isinstance(value, str):
            return value
        if value is not None and hasattr(value, "name"):
            return value.name
    return "UNKNOWN"


def _leader(receipt) -> dict:
    data = receipt.get("consensus_data") or {}
    leader = data.get("leader_receipt") or {}
    if isinstance(leader, list):
        leader = leader[0] if leader else {}
    return leader


def _execution(receipt) -> str:
    value = _leader(receipt).get("execution_result")
    return value if isinstance(value, str) else str(value)


def _revert(receipt) -> str:
    result = _leader(receipt).get("result") or {}
    return json.dumps(result)[:400] if not isinstance(result, str) else result[:400]


def _votes(receipt) -> list:
    last = receipt.get("last_round") or {}
    votes = last.get("votes") or (receipt.get("consensus_data") or {}).get("votes") or {}
    if isinstance(votes, dict):
        return [str(v) for v in votes.values()]
    return [str(v) for v in votes]


# -- the chain -----------------------------------------------------------------

class Chain:
    def __init__(self, address: str, transcript: Transcript):
        self.address = address
        self.transcript = transcript
        self.raw_base = transcript.data["raw_base"]
        keys = json.loads(KEYS.read_text(encoding="utf-8"))
        self.accounts = {name: create_account(account_private_key=key)
                         for name, key in keys.items()}
        self.clients = {name: create_client(chain=studionet, account=account,
                                            endpoint=RPC)
                        for name, account in self.accounts.items()}
        self.reader = self.clients[sorted(self.clients)[0]]

    def address_of(self, wallet: str) -> str:
        return str(self.accounts[wallet].address).lower()

    def read(self, method: str, args=None):
        return self.reader.read_contract(address=self.address, function_name=method,
                                         args=args or [])

    def balance(self, address: str) -> int:
        return int(self.reader.get_balance(to_checksum_address(address)))

    def settled_balance(self, address: str, expected: int) -> int:
        """A wallet's balance once a transfer has landed: it lands when the call
        is final, which the node may report a moment before the balance moves."""
        seen = None
        for _poll in range(60):
            seen = self.balance(address)
            if seen == expected:
                break
            time.sleep(5)
        return seen

    def send(self, step: str, wallet: str, method: str, args=None, value: int = 0) -> dict:
        if self.transcript.has(step):
            entry = self.transcript.get(step)
            if entry.get("leader_execution") == "SUCCESS":
                log("  skip " + step + " (recorded " + entry.get("status", "?") + ")")
                return entry
            log("  retry " + step + " (recorded " + str(entry.get("error"))[:80] + ")")
        client = self.clients[wallet]
        log("  " + step + ": " + method + " as " + wallet
            + (" with " + str(value) + " atto" if value else ""))
        kwargs = {"value": value} if value else {}
        tx = client.write_contract(address=self.address, function_name=method,
                                   args=args or [], **kwargs)
        receipt = client.wait_for_transaction_receipt(
            transaction_hash=tx, status=TransactionStatus.FINALIZED, **WAIT)
        entry = {"kind": "write", "method": method, "wallet": wallet,
                 "args": _plain(args or []), "value": str(value), "tx": _hex(tx),
                 "status": _status(receipt), "leader_execution": _execution(receipt),
                 "votes": _votes(receipt), "result": _revert(receipt)}
        if entry["leader_execution"] != "SUCCESS":
            entry["error"] = _revert(receipt)
        self.transcript.put(step, entry)
        log("    " + entry["status"] + "/" + entry["leader_execution"] + " votes "
            + ",".join(entry["votes"]))
        return entry

    def must(self, step: str, wallet: str, method: str, args=None, value: int = 0) -> dict:
        entry = self.send(step, wallet, method, args, value)
        if entry.get("leader_execution") != "SUCCESS":
            raise SystemExit("  " + step + " did not execute: " + str(entry.get("error")))
        return entry

    def refuse(self, step: str, wallet: str, method: str, args=None, because: str = "",
               expect: str = "") -> dict:
        """A write that must be refused - and for the reason it was sent to test:
        a refusal for some other reason is recorded as a miss."""
        if self.transcript.has(step):
            log("  skip " + step + " (recorded)")
            return self.transcript.get(step)
        client = self.clients[wallet]
        log("  " + step + ": expecting a refusal of " + method)
        entry = {"kind": "refusal", "method": method, "wallet": wallet,
                 "args": _plain(args or []), "because": because, "expect": expect}
        try:
            tx = client.write_contract(address=self.address, function_name=method,
                                       args=args or [])
            receipt = client.wait_for_transaction_receipt(
                transaction_hash=tx, status=TransactionStatus.FINALIZED, **WAIT)
            entry["tx"] = _hex(tx)
            entry["status"] = _status(receipt)
            entry["leader_execution"] = _execution(receipt)
            entry["error"] = _revert(receipt)
            entry["held"] = entry["leader_execution"] != "SUCCESS"
        except Exception as err:                       # a client-side rejection counts
            entry["error"] = str(err)[:400]
            entry["held"] = True
        if entry["held"] and expect not in str(entry.get("error", "")):
            entry["held"] = False
            entry["wrong_reason"] = True
        self.transcript.put(step, entry)
        log("    refused" if entry["held"] else
            ("    REFUSED FOR ANOTHER REASON - recorded as a miss" if entry.get("wrong_reason")
             else "    NOT REFUSED - recorded as a miss"))
        return entry

    def returned(self, step: str, wallet: str, method: str, args: list, value: int,
                 expect: str, because: str):
        """A payable call that must be refused with value attached: it executes,
        says why, and the value is back in the wallet when the call is final."""
        if self.transcript.has(step) and "held" in self.transcript.get(step):
            log("  skip " + step + " (recorded)")
            return
        address = self.address_of(wallet)
        custody = self.read("get_stats")["custody"]
        before = self.balance(address)
        entry = self.must(step, wallet, method, args, value=value)
        after = self.settled_balance(address, before)
        entry["kind"] = "refusal"
        entry["because"] = because
        entry["expect"] = expect
        entry["error"] = entry["result"]
        self.transcript.put(step, entry)
        held = after == before and "RETURNED" in entry["result"] \
            and expect in entry["result"] and self.read("get_stats")["custody"] == custody
        entry["held"] = held
        entry["balance_change"] = str(after - before)
        self.transcript.put(step, entry)
        log("    value returned" if held else "    VALUE NOT RETURNED - recorded as a miss")

    def check(self, step: str, held: bool, **fields):
        entry = self.transcript.get(step) if self.transcript.has(step) else {"kind": "check"}
        entry.update(fields)
        entry["held"] = bool(held)
        self.transcript.put(step, entry)
        log("    " + step + (" HELD" if held else " MISSED") + " "
            + json.dumps({k: v for k, v in fields.items() if k != "note"})[:200])


# -- where the deliverables are served -----------------------------------------------

def mirror(raw_base: str) -> str:
    prefix = "https://raw.githubusercontent.com/"
    if not raw_base.startswith(prefix):
        sys.exit("--raw-base must be a commit-pinned raw.githubusercontent.com URL")
    owner, repo, commit, rest = raw_base[len(prefix):].split("/", 3)
    return "https://cdn.jsdelivr.net/gh/" + owner + "/" + repo + "@" + commit + "/" + rest


def branch_base(raw_base: str, branch: str) -> str:
    prefix = "https://raw.githubusercontent.com/"
    owner, repo, _commit, rest = raw_base[len(prefix):].split("/", 3)
    return prefix + owner + "/" + repo + "/" + branch + "/" + rest


def submission_of(case: dict, cases: dict, raw_base: str) -> str:
    document = case["docs"]["document"]
    if case["docs_served"] == "branch":
        urls = [branch_base(raw_base, cases["evidence_branch"]) + document]
    else:
        urls = [mirror(raw_base) + document, raw_base + document]
    api = cases["unreachable_base"] if case["api"] == "unreachable" \
        else raw_base + case["api"]
    return json.dumps({"fields": {"api": api, "docs": {"sha256": case["docs"]["sha256"],
                                                      "urls": urls}}}, sort_keys=True)


def fetch_status(url: str):
    """(http status, sha256 of the body) as this machine sees a URL."""
    request = urllib.request.Request(url, headers={"User-Agent": "bounty-acceptance-run",
                                                   "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return (response.status, hashlib.sha256(response.read()).hexdigest())
    except urllib.error.HTTPError as err:
        return (err.code, "")
    except Exception:
        return (0, "")


def git(*args, cwd=None) -> str:
    done = subprocess.run(["git", *args], cwd=cwd or BRANCH_CLONE, capture_output=True,
                          text=True)
    if done.returncode != 0:
        raise SystemExit("git " + " ".join(args) + " failed: " + done.stderr[:300])
    return done.stdout.strip()


def branch_serve(cases: dict, present: list, message: str):
    """Make the evidence branch hold exactly these documents, and push it. The
    branch has no history in common with main: it is where the run keeps the
    documents it takes down and brings back."""
    branch = cases["evidence_branch"]
    origin = git("remote", "get-url", "origin", cwd=ROOT)
    if not (BRANCH_CLONE / ".git").exists():
        BRANCH_CLONE.mkdir(parents=True, exist_ok=True)
        git("init", "-q", "-b", branch)
        git("remote", "add", "origin", origin)
        for key in ("user.name", "user.email"):
            git("config", key, git("config", key, cwd=ROOT))
        git("config", "core.autocrlf", "false")
    for path in (BRANCH_CLONE / "fixtures").rglob("*"):
        if path.is_file():
            path.unlink()
    for document in present:
        target = BRANCH_CLONE / "fixtures" / document
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((FIXTURES / document).read_bytes())
    (BRANCH_CLONE / "README.md").write_text(
        "Documents the live run serves from a branch so that it can take them down and"
        " bring them back. TEST / DEMONSTRATION ONLY.\n", encoding="utf-8", newline="\n")
    git("add", "-A")
    if git("status", "--porcelain") != "":
        git("commit", "-q", "-m", message)
    git("push", "-q", "-f", "origin", branch)


def wait_served(url: str, want_status: int, want_sha: str = "", limit: int = 900) -> bool:
    """Wait until a branch URL answers as it should: the raw host caches a
    branch for some minutes."""
    start = time.time()
    while time.time() - start < limit:
        status, digest = fetch_status(url)
        if status == want_status and (want_sha == "" or digest == want_sha):
            return True
        log("  waiting for " + url.rsplit("/", 1)[-1] + " to answer " + str(want_status)
            + " (now " + str(status) + ")")
        time.sleep(30)
    return False


# -- ids -----------------------------------------------------------------------

def bounty_id_of(chain: Chain, key: str) -> str:
    return chain.transcript.get("create:" + key)["bounty_id"]


def submission_id_of(chain: Chain, code: str) -> str:
    return chain.transcript.get("submit:" + code)["submission_id"]


def by_code(cases: dict) -> dict:
    return {c["case"]: c for c in cases["cases"]}


# -- the phases ----------------------------------------------------------------

def phase_wallets(chain: Chain, cases: dict):
    wanted = {"sponsor": SPONSOR_FUNDING, "stranger": DEVELOPER_FUNDING}
    for case in cases["cases"]:
        wanted[case["developer"]] = DEVELOPER_FUNDING
    for wallet, amount in wanted.items():
        step = "faucet:" + wallet
        if chain.transcript.has(step) and "held" in chain.transcript.get(step):
            log("  skip " + step + " (recorded)")
            continue
        address = chain.address_of(wallet)
        before = chain.balance(address)
        tx = chain.clients[wallet].fund_account(to_checksum_address(address), amount)
        after = before
        for _poll in range(60):
            after = chain.balance(address)
            if after >= before + amount:
                break
            time.sleep(5)
        chain.transcript.put(step, {"kind": "funding", "tx": _hex(tx),
                                    "before": str(before), "after": str(after)})
        chain.check(step, after >= before + amount, funded=str(after - before))


def phase_bounties(chain: Chain, cases: dict, template: dict):
    reward = template["reward"]
    for key, plan in cases["bounties"].items():
        step = "create:" + key
        if not chain.transcript.done(step):
            definition = json.loads(json.dumps(template))
            definition["submit_by"] = now_iso(plan["lasts"])
            definition["contest_window"] = plan["contest_window"]
            definition["evaluate_window"] = plan["evaluate_window"]
            entry = chain.must(step, "sponsor", "create_bounty",
                               [json.dumps(definition, sort_keys=True)])
            total = chain.read("get_stats")["bounties"]
            entry["bounty_id"] = chain.read("list_bounties", [total - 1, 1])["ids"][0]
            entry["submit_by"] = definition["submit_by"]
            chain.transcript.put(step, entry)
        bounty_id = bounty_id_of(chain, key)
        got = chain.read("get_bounty", [bounty_id])
        if "held" not in chain.transcript.get(step):
            chain.check(step, got["found"] and got["sponsor"] == chain.address_of("sponsor")
                        and got["reward"] == str(reward), bounty_id=bounty_id,
                        bounty_hash=got["bounty_hash"], note=plan["note"])
        if key == "cancelled_unfunded":
            continue
        step = "fund:" + key
        if not chain.transcript.done(step):
            chain.must(step, "sponsor", "fund", [bounty_id], value=reward)
        if "held" not in chain.transcript.get(step):
            got = chain.read("get_bounty", [bounty_id])
            chain.check(step, got["status"] in ("FUNDED", "RELEASED", "RETURNED"),
                        status=got["status"])
    # the two that are given up
    for key, expect, why in (("cancelled_unfunded", "CANCELLED", "CANCELLED_BEFORE_FUNDING"),
                             ("cancelled_funded", "RETURNED",
                              "CANCELLED_BEFORE_ANY_SUBMISSION")):
        step = "cancel:" + key
        bounty_id = bounty_id_of(chain, key)
        chain.must(step, "sponsor", "cancel", [bounty_id])
        if "held" not in chain.transcript.get(step):
            outcome = chain.read("get_outcome", [bounty_id])
            chain.check(step, outcome["status"] == expect and outcome["closed_by"] == why,
                        status=outcome["status"], closed_by=outcome["closed_by"])


def phase_submit(chain: Chain, cases: dict, template: dict, raw_base: str):
    # the documents the run will later take down are served from a branch
    step = "outage:serving"
    if not chain.transcript.has(step):
        on_branch = [c["docs"] for c in cases["cases"] if c["docs_served"] == "branch"]
        branch_serve(cases, [d["document"] for d in on_branch], "Serve the documents")
        base = branch_base(raw_base, cases["evidence_branch"])
        served = all(wait_served(base + d["document"], 200, d["sha256"]) for d in on_branch)
        chain.check(step, served, note="the branch serves both documents")
    for case in cases["cases"]:
        code = case["case"]
        step = "submit:" + code
        bounty_id = bounty_id_of(chain, case["bounty"])
        if not chain.transcript.done(step):
            payload = submission_of(case, cases, raw_base)
            entry = chain.must(step, case["developer"], "submit", [bounty_id, payload],
                               value=template["bond"])
            listed = chain.read("list_submissions", [bounty_id, 0, 50])["ids"]
            mine = [sid for sid in listed if chain.read(
                "get_submission", [sid])["developer"] == chain.address_of(case["developer"])]
            entry["submission_id"] = mine[-1]
            chain.transcript.put(step, entry)
        if "held" not in chain.transcript.get(step):
            got = chain.read("get_submission", [submission_id_of(chain, code)])
            chain.check(step, got["bounty_id"] == bounty_id
                        and got["bond"] == str(template["bond"]),
                        sequence=got["sequence"], read_by=got["read_by"])


def record_round(chain: Chain, step: str, submission_id: str, case: dict, expect: str):
    """Read the round a step asked for off the chain and check it. `expect` is
    read (the catalogue's outcome and states), unread (decided nothing), or an
    outcome name."""
    history = chain.read("get_history", [submission_id])["rounds"]
    record = chain.read("get_evaluation", [history[-1]["evaluation_id"]])["evaluation"]
    entry = chain.transcript.get(step)
    entry.update(evaluation_id=record["evaluation_id"], round=record["round"],
                 mode=record["mode"], applied=record["applied"],
                 observed_outcome=record["outcome"], observed_reason=record["reason_code"],
                 observed_states=record["states"], panel_state=record["panel_state"],
                 probes=[p["result"] for p in record["probes"]], markers=record["markers"],
                 served_by=[x.get("served_by", "") for x in record["sources"]],
                 note=case["note"])
    chain.transcript.put(step, entry)
    if expect == "unread":
        held = record["applied"] is False and record["states"] == {}
        if "expect_round_reason" in case:
            held = held and record["reason_code"] == case["expect_round_reason"]
        chain.check(step, held, expected="recorded, not applied",
                    reason=record["reason_code"])
        return
    held = record["applied"] is True and record["outcome"] == case["expect_outcome"] \
        and record["states"] == case["expect_states"]
    if "expect_round_reason" in case:
        held = held and record["reason_code"] == case["expect_round_reason"]
    chain.check(step, held, expected_outcome=case["expect_outcome"],
                expected_states=case["expect_states"])


def phase_evaluate(chain: Chain, cases: dict):
    for case in cases["cases"]:
        code = case["case"]
        if code in ("Q1", "R4"):                   # read late, and never read
            continue
        step = "evaluate:" + code
        submission_id = submission_id_of(chain, code)
        if not chain.transcript.done(step):
            chain.must(step, "keeper", "evaluate", [submission_id])
        if "held" not in chain.transcript.get(step):
            record_round(chain, step, submission_id, case,
                         "unread" if code == "R5" else "read")


def phase_contest(chain: Chain, cases: dict):
    """The sponsor contests an acceptance and it is upheld; a developer contests
    a rejection and it stands."""
    known = by_code(cases)
    for code, wallet in (("Q2", "sponsor"), ("R1", known["R1"]["developer"])):
        step = "contest:" + code
        submission_id = submission_id_of(chain, code)
        if not chain.transcript.done(step):
            chain.must(step, wallet, "contest", [submission_id])
        if "held" not in chain.transcript.get(step):
            record_round(chain, step, submission_id, known[code], "read")


def phase_outage(chain: Chain, cases: dict, raw_base: str):
    """Two accepted submissions lose their documentation while the sponsor
    contests them. One developer brings its document back and a round reads it
    again; the other never does."""
    known = by_code(cases)
    base = branch_base(raw_base, cases["evidence_branch"])
    kept, lost = known["D1"], known["D2"]
    step = "outage:down"
    if not chain.transcript.has(step):
        branch_serve(cases, [], "Take the documents down")
        gone = wait_served(base + kept["docs"]["document"], 404) \
            and wait_served(base + lost["docs"]["document"], 404)
        chain.check(step, gone, note="the branch no longer serves either document")
    for code in ("D1", "D2"):
        step = "contest:" + code
        submission_id = submission_id_of(chain, code)
        if not chain.transcript.done(step):
            chain.must(step, "sponsor", "contest", [submission_id])
        if "held" not in chain.transcript.get(step):
            record_round(chain, step, submission_id, known[code], "unread")
            got = chain.read("get_submission", [submission_id])
            entry = chain.transcript.get(step)
            chain.check(step, entry["held"] and got["in_doubt"] is True
                        and got["verdict"] == "ACCEPTED", in_doubt=got["in_doubt"],
                        verdict=got["verdict"])
    doubted = submission_id_of(chain, "D1")
    chain.refuse("refuse:contest_in_doubt", "sponsor", "contest", [doubted],
                 because="while an acceptance is in doubt nobody contests it",
                 expect="the acceptance is in doubt")
    chain.refuse("refuse:restore_by_another", "keeper", "restore", [doubted],
                 because="only the developer restores its deliverable",
                 expect="only the developer restores")
    step = "outage:restored"
    if not chain.transcript.has(step):
        branch_serve(cases, [kept["docs"]["document"]], "Bring one document back")
        back = wait_served(base + kept["docs"]["document"], 200, kept["docs"]["sha256"])
        chain.check(step, back, note="the branch serves the first document again")
    step = "restore:D1"
    if not chain.transcript.done(step):
        chain.must(step, kept["developer"], "restore", [doubted])
    if "held" not in chain.transcript.get(step):
        record_round(chain, step, doubted, kept, "read")
        got = chain.read("get_submission", [doubted])
        entry = chain.transcript.get(step)
        chain.check(step, entry["held"] and got["in_doubt"] is False,
                    in_doubt=got["in_doubt"], verdict=got["verdict"])


def phase_queue(chain: Chain, cases: dict):
    """The second submission stands accepted and its window has passed, and it
    is still not paid: the first, though nobody has read it yet, is first in
    line. Read late, the first is rejected; when its own window has passed the
    second is paid and the third, also accepted, is outrun."""
    known = by_code(cases)
    first, second, third = (submission_id_of(chain, c) for c in ("Q1", "Q2", "Q3"))
    wait_until(chain.read("get_submission", [second])["window_ends"],
               "the contest window of " + second)
    chain.refuse("refuse:paid_before_an_unread_earlier_one", "keeper", "finalize", [second],
                 because="an earlier submission that may still be read is first in line",
                 expect="first in line: " + first)
    step = "evaluate:Q1"
    if not chain.transcript.done(step):
        chain.must(step, "keeper", "evaluate", [first])
    if "held" not in chain.transcript.get(step):
        record_round(chain, step, first, known["Q1"], "read")
    chain.refuse("refuse:paid_before_an_earlier_rejection_is_final", "keeper", "finalize",
                 [second],
                 because="an earlier rejection is first in line while it can be contested",
                 expect="first in line: " + first)
    wait_until(chain.read("get_submission", [first])["window_ends"],
               "the contest window of " + first)
    chain.refuse("refuse:later_acceptance_before_the_earlier", "keeper", "finalize", [third],
                 because="of two accepted submissions the earlier is paid",
                 expect="first in line: " + second)
    step = "finalize:Q2"
    if not chain.transcript.done(step):
        chain.must(step, "keeper", "finalize", [second])
    if "held" not in chain.transcript.get(step):
        outcome = chain.read("get_outcome", [bounty_id_of(chain, "queue")])
        chain.check(step, outcome["released"] is True
                    and outcome["winner"] == chain.address_of(known["Q2"]["developer"])
                    and outcome["winning_submission"] == second,
                    winner=outcome["winner"], status=outcome["status"])


def final_check(chain: Chain, code: str, case: dict):
    step = "final:" + code
    if chain.transcript.has(step) and "held" in chain.transcript.get(step):
        return
    got = chain.read("get_submission", [submission_id_of(chain, code)])
    chain.check(step, got["final"] is True and got["result"] == case["expect_result"]
                and got["result_reason"] == case["expect_reason"]
                and got["bond_fate"] == case["expect_bond"],
                result=got["result"], result_reason=got["result_reason"],
                bond_fate=got["bond_fate"], note=case["note"])


def phase_settle(chain: Chain, cases: dict):
    known = by_code(cases)
    for code in ("R1", "R2", "R3", "D2", "D1"):
        step = "finalize:" + code
        submission_id = submission_id_of(chain, code)
        if not chain.transcript.done(step):
            wait_until(chain.read("get_submission", [submission_id])["window_ends"],
                       "the contest window of " + submission_id)
            chain.must(step, "keeper", "finalize", [submission_id])
    for code in ("Q1", "Q2", "Q3", "R1", "R2", "R3", "D1", "D2"):
        final_check(chain, code, known[code])
    step = "released:doubt_restored"
    if not chain.transcript.has(step):
        outcome = chain.read("get_outcome", [bounty_id_of(chain, "doubt_restored")])
        chain.check(step, outcome["released"] is True
                    and outcome["winner"] == chain.address_of(known["D1"]["developer"]),
                    status=outcome["status"], winner=outcome["winner"])


def phase_lapse(chain: Chain, cases: dict):
    known = by_code(cases)
    for code in ("R4", "R5"):
        step = "lapse:" + code
        submission_id = submission_id_of(chain, code)
        if not chain.transcript.done(step):
            wait_until(chain.read("get_submission", [submission_id])["read_by"],
                       "the read-by time of " + submission_id)
            chain.must(step, "keeper", "lapse", [submission_id])
        final_check(chain, code, known[code])


def phase_close(chain: Chain, cases: dict):
    for key in ("returned", "doubt_lost"):
        step = "close:" + key
        bounty_id = bounty_id_of(chain, key)
        if not chain.transcript.done(step):
            wait_until(chain.transcript.get("create:" + key)["submit_by"],
                       "the deadline of " + bounty_id)
            chain.must(step, "keeper", "close", [bounty_id])
        if "held" not in chain.transcript.get(step):
            outcome = chain.read("get_outcome", [bounty_id])
            chain.check(step, outcome["returned"] is True
                        and outcome["closed_by"] == "NO_ACCEPTED_SUBMISSION",
                        status=outcome["status"], closed_by=outcome["closed_by"])


def phase_withdraw(chain: Chain, cases: dict, template: dict):
    """Every balance the contract owes is paid out, and each payment is what the
    run says it should be."""
    reward, bond = template["reward"], template["bond"]
    known = by_code(cases)
    expected = {"sponsor": 0}
    for key, plan in cases["bounties"].items():
        if plan["ends"] == "RETURNED":
            expected["sponsor"] += reward
    for case in cases["cases"]:
        wallet = case["developer"]
        expected.setdefault(wallet, 0)
        if case["expect_bond"] == "FORFEITED_TO_SPONSOR":
            expected["sponsor"] += bond
        else:
            expected[wallet] += bond
        if case["expect_result"] == "PAID":
            expected[wallet] += reward
    for wallet, amount in sorted(expected.items()):
        step = "withdraw:" + wallet
        address = chain.address_of(wallet)
        if amount == 0:
            if not chain.transcript.has(step):
                owed = chain.read("get_balance", [address])["balance"]
                chain.check(step, owed == "0", owed=owed, expected="0")
            continue
        if not chain.transcript.done(step):
            owed = chain.read("get_balance", [address])["balance"]
            before = chain.balance(address)
            entry = chain.must(step, wallet, "withdraw", [])
            entry["owed"] = owed
            entry["before"] = str(before)
            chain.transcript.put(step, entry)
        entry = chain.transcript.get(step)
        if "held" in entry:
            continue
        before = int(entry["before"])
        after = chain.settled_balance(address, before + amount)
        chain.check(step, entry["owed"] == str(amount) and after - before == amount
                    and chain.read("get_balance", [address])["balance"] == "0",
                    expected=str(amount), owed=entry["owed"], received=str(after - before))
    assert known


def phase_refusals(chain: Chain, cases: dict, template: dict, raw_base: str):
    known = by_code(cases)
    reward, bond = template["reward"], template["bond"]
    paid = submission_id_of(chain, "Q2")
    released = bounty_id_of(chain, "queue")
    definition = json.loads(json.dumps(template))
    definition["submit_by"] = now_iso(3 * 86400)

    def changed(**fields):
        out = json.loads(json.dumps(definition))
        out.update(fields)
        return json.dumps(out, sort_keys=True)

    conflicting = json.loads(json.dumps(definition["requirements"]))
    conflicting[2]["check"]["path"] = conflicting[0]["check"]["path"]
    chain.refuse("refuse:create_conflicting_checks", "sponsor", "create_bounty",
                 [changed(requirements=conflicting)],
                 because="a bounty nobody could win is refused",
                 expect="one path answers with one status")
    chain.refuse("refuse:create_bond_above_reward", "sponsor", "create_bounty",
                 [changed(bond=reward + 1)], because="a bond cannot exceed the reward",
                 expect="bond must be 0 to the reward")
    chain.refuse("refuse:create_deadline_too_near", "sponsor", "create_bounty",
                 [changed(submit_by=now_iso(600))],
                 because="a bounty must stay open long enough to be answered",
                 expect="submit_by must be at least 3600 seconds from now")
    chain.refuse("refuse:create_secret_in_path", "sponsor", "create_bounty",
                 [changed(requirements=[dict(definition["requirements"][0], check=dict(
                     definition["requirements"][0]["check"], path="/v1/status?key=abc"))])],
                 because="a path carries no query: a secret in a bounty is given away",
                 expect="check path may hold letters, digits and")

    # an open bounty for the rules of a live submission
    step = "create:refusals"
    if not chain.transcript.done(step):
        entry = chain.must(step, "sponsor", "create_bounty", [changed()])
        total = chain.read("get_stats")["bounties"]
        entry["bounty_id"] = chain.read("list_bounties", [total - 1, 1])["ids"][0]
        chain.transcript.put(step, entry)
    open_bounty = chain.transcript.get(step)["bounty_id"]
    good = submission_of(known["Q2"], cases, raw_base)
    chain.returned("returned:fund_wrong_amount", "sponsor", "fund", [open_bounty],
                   reward - 1, "the value sent must be exactly the reward",
                   "a funding of the wrong amount goes back")
    chain.returned("returned:fund_by_another", "stranger", "fund", [open_bounty], bond,
                   "only the sponsor funds its bounty",
                   "somebody else's funding goes back")
    chain.refuse("refuse:submit_to_unfunded", known["Q3"]["developer"], "submit",
                 [open_bounty, good], because="nothing is submitted to an unfunded bounty",
                 expect="only a FUNDED bounty takes submissions")
    chain.must("fund:refusals", "sponsor", "fund", [open_bounty], value=reward)
    chain.returned("returned:fund_twice", "sponsor", "fund", [open_bounty], reward,
                   "only a CREATED bounty is funded", "a second funding goes back")
    chain.returned("returned:submit_wrong_bond", known["Q3"]["developer"], "submit",
                   [open_bounty, good], bond + 1, "the value sent must be exactly the bond",
                   "a submission with the wrong bond goes back")
    chain.returned("returned:sponsor_submits", "sponsor", "submit", [open_bounty, good],
                   bond, "a sponsor does not submit to its own bounty",
                   "a sponsor's own submission goes back")
    outside = json.loads(good)
    outside["fields"]["api"] = "https://api.elsewhere.example.com/timetable"
    chain.returned("returned:submit_outside_hosts", known["Q3"]["developer"], "submit",
                   [open_bounty, json.dumps(outside, sort_keys=True)], bond,
                   "host is outside the bounty's endpoint hosts",
                   "a deliverable hosted where the bounty does not allow goes back")
    step = "submit:REFUSALS"
    if not chain.transcript.done(step):
        entry = chain.must(step, known["Q3"]["developer"], "submit", [open_bounty, good],
                           value=bond)
        entry["submission_id"] = chain.read("list_submissions",
                                            [open_bounty, 0, 50])["ids"][-1]
        chain.transcript.put(step, entry)
    live = chain.transcript.get(step)["submission_id"]
    chain.returned("returned:submit_while_one_is_open", known["Q3"]["developer"], "submit",
                   [open_bounty, good], bond, "already has a submission of yours",
                   "a second submission while one is open goes back")
    chain.refuse("refuse:cancel_with_a_submission", "sponsor", "cancel", [open_bounty],
                 because="a bounty somebody has answered is not cancelled",
                 expect="before any submission was made to it")
    chain.refuse("refuse:close_before_deadline", "keeper", "close", [open_bounty],
                 because="a bounty is closed only after its deadline",
                 expect="open to submissions until")
    chain.refuse("refuse:mirror_by_another", "keeper", "add_mirror",
                 [live, "docs", raw_base + "evidence/docs-complete.html"],
                 because="only the developer says where its documents are served",
                 expect="only the developer adds an address")
    chain.refuse("refuse:lapse_too_early", "keeper", "lapse", [live],
                 because="a submission lapses only after its read-by time",
                 expect="this submission may be read until")
    chain.refuse("refuse:contest_before_evaluation", known["Q3"]["developer"], "contest",
                 [live], because="there is nothing to contest before an evaluation",
                 expect="only an EVALUATED submission is contested")
    if not chain.transcript.done("evaluate:REFUSALS"):
        chain.must("evaluate:REFUSALS", "keeper", "evaluate", [live])
    chain.refuse("refuse:contest_by_stranger", "keeper", "contest", [live],
                 because="only the developer or the sponsor contests",
                 expect="only the developer or the sponsor contests")
    chain.refuse("refuse:developer_contests_acceptance", known["Q3"]["developer"],
                 "contest", [live], because="a developer contests a rejection",
                 expect="a developer contests a rejection")
    chain.refuse("refuse:restore_without_doubt", known["Q3"]["developer"], "restore",
                 [live], because="there is no doubt to answer",
                 expect="only an acceptance in doubt is restored")
    chain.refuse("refuse:finalize_too_early", "keeper", "finalize", [live],
                 because="a verdict is final only after its contest window",
                 expect="the contest window closes at")
    # what is over stays over
    chain.refuse("refuse:evaluate_final", "keeper", "evaluate", [paid],
                 because="a final submission is not evaluated again",
                 expect="only a SUBMITTED submission is evaluated")
    chain.refuse("refuse:contest_final", "sponsor", "contest", [paid],
                 because="a final verdict is not contested",
                 expect="only an EVALUATED submission is contested")
    chain.refuse("refuse:mirror_after_final", known["Q2"]["developer"], "add_mirror",
                 [paid, "docs", raw_base + "evidence/docs-thin.html"],
                 because="a final submission takes no more addresses",
                 expect="an address is added to a submission that is not yet final")
    chain.returned("returned:submit_to_released", known["R4"]["developer"], "submit",
                   [released, good], bond, "only a FUNDED bounty takes submissions",
                   "a submission to a bounty that was paid goes back")
    chain.refuse("refuse:close_released", "keeper", "close", [released],
                 because="a bounty is closed once", expect="only a FUNDED bounty is closed")
    chain.refuse("refuse:withdraw_nothing", "stranger", "withdraw", [],
                 because="nothing is owed to a stranger", expect="nothing to withdraw")
    chain.refuse("refuse:unknown_submission", "keeper", "finalize", ["SUB-999999"],
                 because="no such submission", expect="unknown submission_id")
    # the open bounty is finished properly: its acceptance is paid
    step = "finalize:REFUSALS"
    if not chain.transcript.done(step):
        wait_until(chain.read("get_submission", [live])["window_ends"],
                   "the contest window of " + live)
        chain.must(step, "keeper", "finalize", [live])
    step = "withdraw:refusals"
    developer = chain.address_of(known["Q3"]["developer"])
    if not chain.transcript.done(step):
        before = chain.balance(developer)
        entry = chain.must(step, known["Q3"]["developer"], "withdraw", [])
        entry["before"] = str(before)
        chain.transcript.put(step, entry)
    entry = chain.transcript.get(step)
    if "held" not in entry:
        after = chain.settled_balance(developer, int(entry["before"]) + reward + bond)
        chain.check(step, after - int(entry["before"]) == reward + bond,
                    received=str(after - int(entry["before"])))


def phase_custody(chain: Chain):
    """The contract's balance on chain is what it holds in escrow plus the
    balances it owes, exactly - and at the end of the run both are nothing."""
    stats = chain.read("get_stats")
    held = chain.settled_balance(chain.address, int(stats["custody"]))
    chain.check("custody", held == int(stats["custody"]) and stats["custody"] == "0",
                contract_balance=str(held), custody=stats["custody"],
                escrow_held=stats["escrow_held"], balances_owed=stats["balances_owed"],
                returned_calls=stats["returned_calls"])
    chain.transcript.data["stats"] = stats
    chain.transcript.save()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("address")
    parser.add_argument("--raw-base", required=True)
    parser.add_argument("--phase", default="full", choices=PHASES + ("full",))
    parser.add_argument("--transcript", default="")
    args = parser.parse_args()
    path = None
    if args.transcript:
        path = pathlib.Path(args.transcript)
        path.parent.mkdir(parents=True, exist_ok=True)
        globals()["LOG"] = path.with_suffix(".log")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    raw_base = args.raw_base if args.raw_base.endswith("/") else args.raw_base + "/"
    transcript = Transcript(args.address, raw_base, path)
    template = json.loads((FIXTURES / "bounty.json").read_text(encoding="utf-8"))
    cases = json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))
    chain = Chain(args.address, transcript)
    log("contract " + args.address + " phase " + args.phase)
    phases = PHASES if args.phase == "full" else (args.phase,)
    for phase in phases:
        log("phase " + phase)
        if phase == "wallets":
            phase_wallets(chain, cases)
        elif phase == "bounties":
            phase_bounties(chain, cases, template)
        elif phase == "submit":
            phase_submit(chain, cases, template, raw_base)
        elif phase == "evaluate":
            phase_evaluate(chain, cases)
        elif phase == "contest":
            phase_contest(chain, cases)
        elif phase == "outage":
            phase_outage(chain, cases, raw_base)
        elif phase == "queue":
            phase_queue(chain, cases)
        elif phase == "settle":
            phase_settle(chain, cases)
        elif phase == "lapse":
            phase_lapse(chain, cases)
        elif phase == "close":
            phase_close(chain, cases)
        elif phase == "withdraw":
            phase_withdraw(chain, cases, template)
        elif phase == "refusals":
            phase_refusals(chain, cases, template, raw_base)
        elif phase == "custody":
            phase_custody(chain)
    log("summary " + json.dumps(transcript.summary()))


if __name__ == "__main__":
    main()

"""Reads against the canonical StudioNet deployment.

Nothing here asks for a consensus round, moves money or changes state. The
tests check that the contract on chain is the contract in this repository, that
its surface is the documented one, that what the live run recorded is what the
chain still answers, and that the contract's balance is the custody it reports.

Each test is independently runnable:

    python -m pytest tests/integration -q
    python -m pytest tests/integration -q -k source_is_this_repository
"""

import base64
import hashlib
import json
import pathlib
import sys
import time
import urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

RECORD = ROOT / "deploy" / "deployment.json"
TRANSCRIPT = ROOT / "deploy" / "live_run_transcript.json"
CONTRACT = ROOT / "contracts" / "bounty_acceptance.py"
CASES = ROOT / "fixtures" / "cases.json"
WALLETS = ROOT / "fixtures" / "wallets.json"
RPC = "https://studio.genlayer.com/api"
WRITES = ("create_bounty", "fund", "cancel", "close", "submit", "add_mirror", "evaluate",
          "contest", "restore", "finalize", "lapse", "withdraw")
VIEWS = ("get_bounty", "get_outcome", "get_submission", "get_evaluation", "get_history",
         "get_actions", "get_bounty_actions", "get_balance", "list_bounties",
         "list_submissions", "get_stats", "get_config")

pytestmark = pytest.mark.skipif(not RECORD.exists(),
                                reason="no canonical deployment recorded yet")
needs_run = pytest.mark.skipif(not TRANSCRIPT.exists(), reason="no live run recorded yet")


def rpc(method: str, params: list):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode()
    request = urllib.request.Request(RPC, data=body, headers={
        "Content-Type": "application/json", "User-Agent": "ba-integration"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                answer = json.loads(response.read().decode())
            if "error" in answer and "-32029" in json.dumps(answer["error"]):
                raise RuntimeError("rate limited")
            return answer
        except Exception:
            if attempt == 5:
                raise
            time.sleep(5 * (attempt + 1))


@pytest.fixture(scope="module")
def record():
    return json.loads(RECORD.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def client():
    """A read-only client with a throwaway account: nothing under .data/ is needed."""
    import studionet_transport  # noqa: F401 - retries RPC transport failures
    from genlayer_py import create_account, create_client
    from genlayer_py.chains import studionet
    return create_client(chain=studionet, account=create_account(), endpoint=RPC)


def read(client, record, method, args=None):
    return client.read_contract(address=record["contract_address"],
                                function_name=method, args=args or [])


def steps() -> dict:
    return json.loads(TRANSCRIPT.read_text(encoding="utf-8"))["steps"]


def catalogue() -> dict:
    return json.loads(CASES.read_text(encoding="utf-8"))


def cases() -> dict:
    return {c["case"]: c for c in catalogue()["cases"]}


def submissions() -> dict:
    """case code -> submission id, for every catalogue case the run submitted."""
    known = cases()
    return {name.split(":", 1)[1]: entry["submission_id"] for name, entry in steps().items()
            if name.startswith("submit:") and name.split(":", 1)[1] in known}


def bounties() -> dict:
    known = catalogue()["bounties"]
    return {name.split(":", 1)[1]: entry["bounty_id"] for name, entry in steps().items()
            if name.startswith("create:") and name.split(":", 1)[1] in known}


def test_the_deployed_source_is_this_repository(record):
    raw = rpc("gen_getContractCode", [record["contract_address"]]).get("result")
    deployed = str(raw).encode()
    if hashlib.sha256(deployed).hexdigest() != record["source_sha256"]:
        deployed = base64.b64decode(raw)
    assert hashlib.sha256(deployed).hexdigest() == record["source_sha256"]
    assert record["source_sha256"] == hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    assert record["byte_identical"] is True


def test_the_schema_is_the_whole_surface(record):
    schema = rpc("gen_getContractSchema", [record["contract_address"]]).get("result") or {}
    methods = schema.get("methods") or {}
    assert sorted(methods) == sorted(WRITES + VIEWS)
    payable = sorted(name for name in WRITES if methods[name].get("payable") is True)
    assert payable == ["fund", "submit"]


def test_the_config_on_chain_matches_the_contract(client, record):
    config = read(client, record, "get_config")
    header = CONTRACT.read_text(encoding="utf-8").splitlines()[0]
    assert header == "# v" + config["contract_version"]
    assert config["payable"] == ["fund", "submit"]
    assert config["verdicts"] == ["PENDING", "ACCEPTED", "REJECTED"]
    assert config["requirement_states"] == ["MET", "NOT_MET"]
    assert config["reading"]["endpoints_are_live"] is True
    assert config["evaluator_markers"]


def test_the_contract_holds_exactly_what_it_says_it_holds(client, record):
    from eth_utils import to_checksum_address
    stats = read(client, record, "get_stats")
    assert int(stats["custody"]) == int(stats["escrow_held"]) + int(stats["balances_owed"])
    held = int(client.get_balance(to_checksum_address(record["contract_address"])))
    assert held == int(stats["custody"])


@needs_run
def test_every_round_the_run_recorded_is_still_on_chain(client, record):
    checked = 0
    for name, entry in sorted(steps().items()):
        if "evaluation_id" not in entry:
            continue
        evaluation = read(client, record, "get_evaluation", [entry["evaluation_id"]])
        assert evaluation["found"], name
        evaluation = evaluation["evaluation"]
        assert evaluation["outcome"] == entry["observed_outcome"], name
        assert evaluation["reason_code"] == entry["observed_reason"], name
        assert evaluation["states"] == entry["observed_states"], name
        assert evaluation["applied"] == entry["applied"], name
        assert evaluation["mode"] == entry["mode"], name
        checked += 1
    assert checked > 0


@needs_run
def test_every_case_ended_as_the_catalogue_expects(client, record):
    known = cases()
    checked = 0
    for code, submission_id in sorted(submissions().items()):
        case = known[code]
        got = read(client, record, "get_submission", [submission_id])
        assert got["found"] is True and got["final"] is True, code
        assert got["result"] == case["expect_result"], code
        assert got["result_reason"] == case["expect_reason"], code
        assert got["bond_fate"] == case["expect_bond"], code
        checked += 1
    assert checked == len(known)


@needs_run
def test_a_verdict_follows_from_every_requirement(client, record):
    checked = 0
    for name, entry in sorted(steps().items()):
        if "evaluation_id" not in entry or not entry.get("applied"):
            continue
        evaluation = read(client, record, "get_evaluation",
                          [entry["evaluation_id"]])["evaluation"]
        states = evaluation["states"]
        expected = "ACCEPTED" if all(state == "MET" for state in states.values()) \
            else "REJECTED"
        assert evaluation["outcome"] == expected and len(states) == 5, name
        assert evaluation["tally"] == {"requirements": 5, "met": sum(
            1 for state in states.values() if state == "MET")}, name
        checked += 1
    assert checked > 0


@needs_run
def test_each_bounty_ended_where_the_run_took_it_and_only_one_submission_was_paid(
        client, record):
    plans = catalogue()["bounties"]
    wallets = json.loads(WALLETS.read_text(encoding="utf-8"))
    known = cases()
    paid_by_bounty = {}
    for code, submission_id in submissions().items():
        if known[code]["expect_result"] == "PAID":
            paid_by_bounty[known[code]["bounty"]] = (submission_id,
                                                     wallets[known[code]["developer"]])
    for key, bounty_id in sorted(bounties().items()):
        outcome = read(client, record, "get_outcome", [bounty_id])
        assert outcome["status"] == plans[key]["ends"], key
        assert outcome["released"] == (plans[key]["ends"] == "RELEASED"), key
        if outcome["released"]:
            assert (outcome["winning_submission"], outcome["winner"]) == \
                paid_by_bounty[key], key
        else:
            assert outcome["winner"] == "" and key not in paid_by_bounty, key
        listed = read(client, record, "list_submissions", [bounty_id, 0, 50])["ids"]
        results = [read(client, record, "get_submission", [sid])["result"] for sid in listed]
        assert results.count("PAID") == (1 if outcome["released"] else 0), key
        assert read(client, record, "get_bounty", [bounty_id])["open_submissions"] == [], key


@needs_run
def test_the_one_paid_in_the_queue_was_not_the_first_to_be_evaluated(client, record):
    """Priority is the order of submission: the first, read late, was rejected;
    the second was paid; the third, accepted too, was outrun."""
    ids = submissions()
    first, second, third = (read(client, record, "get_submission", [ids[c]])
                            for c in ("Q1", "Q2", "Q3"))
    assert (first["sequence"], second["sequence"], third["sequence"]) == (1, 2, 3)
    assert first["read_at"] > second["read_at"]          # read after the one that was paid
    assert (first["result"], second["result"], third["result"]) == \
        ("REJECTED", "PAID", "OUTRUN")
    assert third["verdict"] == "ACCEPTED"


@needs_run
def test_every_met_by_the_panel_rests_on_a_passage_of_a_pinned_document(client, record):
    checked = 0
    for name, entry in sorted(steps().items()):
        if "evaluation_id" not in entry or not entry.get("applied"):
            continue
        evaluation = read(client, record, "get_evaluation",
                          [entry["evaluation_id"]])["evaluation"]
        sources = {s["evidence_id"]: s for s in evaluation["sources"]}
        for finding in evaluation["findings"]:
            if finding["by"] == "PANEL" and finding["state"] == "MET":
                assert finding["quotes"], name
                for quote in finding["quotes"]:
                    source = sources[quote["evidence_id"]]
                    assert source["raw_sha256"] == source["declared_sha256"], name
                checked += 1
            if finding["by"] == "CODE":
                assert finding["quotes"] == [], name
    assert checked > 0


@needs_run
def test_a_round_that_decided_nothing_changed_nothing(client, record):
    checked = 0
    for name, entry in sorted(steps().items()):
        if entry.get("applied") is not False or "evaluation_id" not in entry:
            continue
        evaluation = read(client, record, "get_evaluation",
                          [entry["evaluation_id"]])["evaluation"]
        assert evaluation["states"] == {} and evaluation["markers"] == [], name
        assert evaluation["findings"] == [], name
        for source in evaluation["sources"]:
            assert "raw_sha256" not in source and "title" not in source, name
        checked += 1
    assert checked > 0


@needs_run
def test_every_refusal_the_run_recorded_was_for_its_own_reason():
    refusals = [entry for entry in steps().values() if entry.get("kind") == "refusal"]
    assert refusals
    for entry in refusals:
        assert entry["held"] is True, entry["step"]
        assert entry["expect"] in entry["error"], entry["step"]


@needs_run
def test_the_run_of_record_missed_nothing():
    summary = json.loads(TRANSCRIPT.read_text(encoding="utf-8"))["summary"]
    assert summary["missed"] == []
    assert summary["outcomes_held"] == summary["outcomes_checked"] > 0
    assert summary["refusals_held"] == summary["refusals"] > 0

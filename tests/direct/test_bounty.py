"""The lifecycle: a bounty, its funding, a submission, an evaluation, and where
the reward and the bond go on each path."""

import json

from tests.direct import support as s

AFTER_WINDOW = "2026-10-05T13:00:01Z"
AFTER_DEADLINE = "2026-10-12T12:00:01Z"


def state(board, submission_id):
    got = board.get_submission(submission_id)
    return (got["status"], got["verdict"], got["result"], got["result_reason"])


# -- the bounty ---------------------------------------------------------------------

def test_a_bounty_is_created_once_and_frozen(board, direct_vm, direct_alice, mod):
    bounty_id = s.created(board, direct_vm, direct_alice)
    got = board.get_bounty(bounty_id)
    assert bounty_id == "BNT-000001" and got["found"] is True
    assert got["sponsor"] == s.hexaddr(direct_alice) and got["status"] == "CREATED"
    assert got["bounty"] == s.bounty()
    assert got["bounty_hash"] == mod._sha256_hex(mod._canonical(s.bounty()))
    assert (got["reward"], got["bond"]) == (str(s.REWARD), str(s.BOND))
    assert board.get_stats()["custody"] == "0"
    assert board.get_bounty("BNT-000404")["found"] is False
    assert board.get_outcome(bounty_id)["open"] is False


def test_funding_holds_exactly_the_reward(board, direct_vm, direct_alice, direct_bob):
    bounty_id = s.created(board, direct_vm, direct_alice)
    assert s.fund(board, direct_vm, direct_alice, bounty_id) == "FUNDED"
    got = board.get_bounty(bounty_id)
    assert got["status"] == "FUNDED" and got["funded_at"] == s.NOW
    stats = board.get_stats()
    assert (stats["escrow_held"], stats["balances_owed"]) == (str(s.REWARD), "0")
    assert s.custody_holds(board, direct_vm)
    assert board.get_outcome(bounty_id)["open"] is True


def test_a_funding_that_is_refused_gives_the_value_back(board, direct_vm, direct_alice,
                                                        direct_bob):
    bounty_id = s.created(board, direct_vm, direct_alice)
    # the wrong amount
    answer = s.fund(board, direct_vm, direct_alice, bounty_id, value=s.REWARD - 1)
    assert answer.startswith("RETURNED: the value sent must be exactly the reward")
    # somebody else's bounty
    answer = s.fund(board, direct_vm, direct_bob, bounty_id)
    assert answer == "RETURNED: only the sponsor funds its bounty"
    # no such bounty
    answer = s.fund(board, direct_vm, direct_alice, "BNT-000404", value=s.REWARD)
    assert answer == "RETURNED: unknown bounty_id"
    assert direct_vm.bank.received[s.hexaddr(direct_alice)] == 2 * s.REWARD - 1
    assert direct_vm.bank.received[s.hexaddr(direct_bob)] == s.REWARD
    assert board.get_bounty(bounty_id)["status"] == "CREATED"
    assert board.get_stats()["returned_calls"] == 3
    assert s.custody_holds(board, direct_vm) and direct_vm.bank.contract == 0
    # funded once; a second funding comes back
    s.fund(board, direct_vm, direct_alice, bounty_id)
    answer = s.fund(board, direct_vm, direct_alice, bounty_id)
    assert answer == "RETURNED: only a CREATED bounty is funded"
    assert s.custody_holds(board, direct_vm) and direct_vm.bank.contract == s.REWARD
    # with nothing attached a refusal is an ordinary failure
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("only a CREATED bounty is funded"):
        board.fund(bounty_id)


def test_a_sponsor_cancels_before_funding_or_before_any_submission(
        board, direct_vm, direct_alice, direct_bob):
    unfunded = s.created(board, direct_vm, direct_alice)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("only the sponsor cancels its bounty"):
        board.cancel(unfunded)
    direct_vm.sender = direct_alice
    assert board.cancel(unfunded) == "CANCELLED"
    assert board.get_outcome(unfunded)["closed_by"] == "CANCELLED_BEFORE_FUNDING"
    funded = s.funded(board, direct_vm, direct_alice)
    assert board.get_bounty_actions(funded, s.NOW)["may_cancel"] is True
    direct_vm.sender = direct_alice
    assert board.cancel(funded) == "RETURNED"
    outcome = board.get_outcome(funded)
    assert (outcome["returned"], outcome["closed_by"]) == \
        (True, "CANCELLED_BEFORE_ANY_SUBMISSION")
    assert s.balance(board, direct_alice) == s.REWARD
    assert s.custody_holds(board, direct_vm)
    with direct_vm.expect_revert("a bounty is cancelled before it is funded"):
        board.cancel(funded)


def test_a_bounty_with_a_submission_is_not_cancelled(board, direct_vm, direct_alice,
                                                     direct_bob):
    submission_id = s.submitted(board, direct_vm, direct_alice, direct_bob)
    bounty_id = board.get_submission(submission_id)["bounty_id"]
    assert board.get_bounty_actions(bounty_id, s.NOW)["may_cancel"] is False
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("before any submission was made to it"):
        board.cancel(bounty_id)


# -- the path the bounty is for -----------------------------------------------------

def test_the_first_deliverable_to_satisfy_every_requirement_takes_the_reward(
        board, direct_vm, direct_alice, direct_bob, direct_charlie):
    submission_id, evaluation_id = s.evaluated(board, direct_vm, direct_alice, direct_bob)
    record = s.record_of(board, evaluation_id)
    assert record["applied"] is True and record["outcome"] == "ACCEPTED"
    assert record["states"] == {"r1": "MET", "r2": "MET", "r3": "MET", "r4": "MET",
                                "r5": "MET"}
    assert record["tally"] == {"requirements": 5, "met": 5}
    assert [p["result"] for p in record["probes"]] == ["ANSWERED"] * 3
    assert state(board, submission_id) == ("EVALUATED", "ACCEPTED", "", "")
    assert s.balance(board, direct_bob) == 0
    # nothing moves inside the contest window
    direct_vm.sender = direct_charlie
    with direct_vm.expect_revert("the contest window closes at 2026-10-05T13:00:00Z"):
        board.finalize(submission_id)
    direct_vm.warp(AFTER_WINDOW)
    assert board.finalize(submission_id) == "PAID"       # anyone may call it
    assert state(board, submission_id) == ("FINAL", "ACCEPTED", "PAID", "EVALUATED")
    assert board.get_submission(submission_id)["bond_fate"] == "RETURNED_TO_DEVELOPER"
    outcome = board.get_outcome("BNT-000001")
    assert outcome["released"] is True and outcome["closed_by"] == "SUBMISSION_ACCEPTED"
    assert (outcome["winner"], outcome["winning_submission"]) == \
        (s.hexaddr(direct_bob), submission_id)
    assert s.balance(board, direct_bob) == s.REWARD + s.BOND
    assert s.balance(board, direct_alice) == 0
    assert s.custody_holds(board, direct_vm)
    direct_vm.sender = direct_bob
    assert board.withdraw() == str(s.REWARD + s.BOND)
    assert direct_vm.bank.received[s.hexaddr(direct_bob)] == s.REWARD + s.BOND
    assert board.get_stats()["custody"] == "0" and direct_vm.bank.contract == 0
    with direct_vm.expect_revert("nothing to withdraw"):
        board.withdraw()


def test_a_deliverable_that_misses_a_requirement_is_rejected_and_the_bounty_stays_open(
        board, direct_vm, direct_alice, direct_bob):
    submission_id, evaluation_id = s.evaluated(
        board, direct_vm, direct_alice, direct_bob, pages={s.DOCS_URL: s.DOCS_THIN},
        said_by_panel=s.answers(r5="NOT_MET"))
    record = s.record_of(board, evaluation_id)
    assert record["outcome"] == "REJECTED" and record["states"]["r5"] == "NOT_MET"
    assert record["tally"] == {"requirements": 5, "met": 4}
    direct_vm.warp(AFTER_WINDOW)
    direct_vm.sender = direct_alice
    assert board.finalize(submission_id) == "REJECTED"
    assert state(board, submission_id) == ("FINAL", "REJECTED", "REJECTED", "EVALUATED")
    # the bond was the price of the place in the queue
    assert board.get_submission(submission_id)["bond_fate"] == "FORFEITED_TO_SPONSOR"
    assert (s.balance(board, direct_alice), s.balance(board, direct_bob)) == (s.BOND, 0)
    assert board.get_outcome("BNT-000001")["open"] is True
    assert s.custody_holds(board, direct_vm)


def test_with_nothing_accepted_by_the_deadline_the_reward_goes_back(
        board, direct_vm, direct_alice, direct_bob, direct_charlie):
    bounty_id = s.funded(board, direct_vm, direct_alice)
    direct_vm.sender = direct_charlie
    with direct_vm.expect_revert("open to submissions until " + s.SUBMIT_BY):
        board.close(bounty_id)
    direct_vm.warp(AFTER_DEADLINE)
    assert board.get_bounty_actions(bounty_id, AFTER_DEADLINE)["may_close"] is True
    assert board.close(bounty_id) == "RETURNED"          # anyone may call it
    outcome = board.get_outcome(bounty_id)
    assert (outcome["returned"], outcome["closed_by"]) == (True, "NO_ACCEPTED_SUBMISSION")
    assert s.balance(board, direct_alice) == s.REWARD
    assert s.custody_holds(board, direct_vm)
    with direct_vm.expect_revert("only a FUNDED bounty is closed"):
        board.close(bounty_id)


def test_a_bounty_is_not_closed_over_an_open_submission(board, direct_vm, direct_alice,
                                                        direct_bob):
    submission_id = s.submitted(board, direct_vm, direct_alice, direct_bob,
                                submit_by="2026-10-05T13:30:00Z", evaluate_window=7200)
    direct_vm.warp("2026-10-05T13:30:01Z")
    with direct_vm.expect_revert("a submission is still open: " + submission_id):
        board.close("BNT-000001")
    # a submission made in time is still evaluated and paid after the deadline
    s.panel(direct_vm, s.answers())
    board.evaluate(submission_id)
    direct_vm.warp("2026-10-05T14:30:02Z")
    assert board.finalize(submission_id) == "PAID"
    assert board.get_outcome("BNT-000001")["released"] is True


# -- who is first -------------------------------------------------------------------

def test_an_accepted_submission_waits_for_an_earlier_one_that_may_still_be_accepted(
        board, direct_vm, direct_alice, direct_bob, direct_charlie):
    first = s.submitted(board, direct_vm, direct_alice, direct_bob, evaluate_window=7200)
    second = s.submit(board, direct_vm, direct_charlie, "BNT-000001")
    s.panel(direct_vm, s.answers())
    direct_vm.sender = direct_charlie
    board.evaluate(second)                               # the later one is read first
    direct_vm.warp(AFTER_WINDOW)
    actions = board.get_actions(second, AFTER_WINDOW)
    assert (actions["may_finalize"], actions["waits_for"]) == (False, first)
    with direct_vm.expect_revert("an earlier submission may still be accepted and is first"
                                 " in line: " + first):
        board.finalize(second)
    # the earlier one is read, and accepted: it is first
    direct_vm.sender = direct_bob
    board.evaluate(first)
    direct_vm.warp("2026-10-05T14:00:02Z")
    with direct_vm.expect_revert("first in line: " + first):
        board.finalize(second)
    assert board.finalize(first) == "PAID"
    assert state(board, second) == ("FINAL", "ACCEPTED", "OUTRUN",
                                    "AN_EARLIER_SUBMISSION_WAS_PAID")
    assert board.get_submission(second)["bond_fate"] == "RETURNED_TO_DEVELOPER"
    assert s.balance(board, direct_bob) == s.REWARD + s.BOND
    assert s.balance(board, direct_charlie) == s.BOND
    assert board.get_outcome("BNT-000001")["winner"] == s.hexaddr(direct_bob)
    assert s.custody_holds(board, direct_vm)
    with direct_vm.expect_revert("only an EVALUATED submission is finalized"):
        board.finalize(second)


def test_an_earlier_submission_nobody_reads_does_not_hold_the_queue(
        board, direct_vm, direct_alice, direct_bob, direct_charlie):
    first = s.submitted(board, direct_vm, direct_alice, direct_bob)
    second = s.submit(board, direct_vm, direct_charlie, "BNT-000001")
    s.panel(direct_vm, s.answers())
    direct_vm.sender = direct_charlie
    board.evaluate(second)
    # one second past both the contest window and the first one's read-by time
    direct_vm.warp(AFTER_WINDOW)
    assert board.get_actions(second, AFTER_WINDOW)["may_finalize"] is True
    assert board.finalize(second) == "PAID"
    # the first ended with the bounty, and as it would have ended anyway: its
    # read-by time had passed, so it was never read and its bond is forfeit
    assert state(board, first) == ("FINAL", "PENDING", "NOT_READ",
                                   "NOT_EVALUATED_BY_READ_BY")
    assert (s.balance(board, direct_bob), s.balance(board, direct_alice)) == (0, s.BOND)
    assert s.custody_holds(board, direct_vm)


def test_a_submission_still_in_time_when_the_bounty_is_paid_keeps_its_bond(
        board, direct_vm, direct_alice, direct_bob, direct_charlie, direct_owner):
    first = s.submitted(board, direct_vm, direct_alice, direct_bob)
    s.panel(direct_vm, s.answers())
    direct_vm.sender = direct_bob
    board.evaluate(first)
    direct_vm.warp("2026-10-05T12:50:00Z")
    unread = s.submit(board, direct_vm, direct_charlie, "BNT-000001")
    rejected = s.submit(board, direct_vm, direct_owner, "BNT-000001",
                        payload=s.submission(api="https://api.other.example.net/ferries"))
    s.serve(direct_vm, "https://api.other.example.net/ferries" + s.STATUS_PATH, "{}")
    s.serve(direct_vm, "https://api.other.example.net/ferries" + s.MISSING_PATH, "x", 404)
    direct_vm.sender = direct_owner
    board.evaluate(rejected)                             # no "routes": rejected, window open
    direct_vm.warp(AFTER_WINDOW)
    assert board.finalize(first) == "PAID"
    for submission_id, verdict in ((unread, "PENDING"), (rejected, "REJECTED")):
        assert state(board, submission_id) == ("FINAL", verdict, "OUTRUN",
                                               "AN_EARLIER_SUBMISSION_WAS_PAID")
    assert s.balance(board, direct_charlie) == s.BOND
    assert s.balance(board, direct_owner) == s.BOND
    assert s.custody_holds(board, direct_vm)


def test_a_submission_nobody_reads_lapses_and_forfeits_its_bond(
        board, direct_vm, direct_alice, direct_bob, direct_charlie):
    submission_id = s.submitted(board, direct_vm, direct_alice, direct_bob)
    direct_vm.sender = direct_charlie
    with direct_vm.expect_revert("this submission may be read until 2026-10-05T13:00:00Z"):
        board.lapse(submission_id)
    direct_vm.warp(AFTER_WINDOW)
    with direct_vm.expect_revert("this submission was to be read by 2026-10-05T13:00:00Z"):
        board.evaluate(submission_id)
    assert board.lapse(submission_id) == "NOT_READ"
    assert state(board, submission_id) == ("FINAL", "PENDING", "NOT_READ",
                                           "NOT_EVALUATED_BY_READ_BY")
    assert s.balance(board, direct_alice) == s.BOND
    assert board.get_bounty("BNT-000001")["open_submissions"] == []
    assert s.custody_holds(board, direct_vm)


# -- submitting ---------------------------------------------------------------------

def test_a_submission_carries_exactly_the_bond(board, direct_vm, direct_alice, direct_bob):
    bounty_id = s.funded(board, direct_vm, direct_alice)
    answer = s.submit(board, direct_vm, direct_bob, bounty_id, value=s.BOND + 1)
    assert answer.startswith("RETURNED: the value sent must be exactly the bond")
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("the value sent must be exactly the bond"):
        board.submit(bounty_id, json.dumps(s.submission()))
    assert board.get_bounty(bounty_id)["submissions"] == 0
    submission_id = s.submit(board, direct_vm, direct_bob, bounty_id)
    got = board.get_submission(submission_id)
    assert (got["sequence"], got["attempt"], got["bond"]) == (1, 1, str(s.BOND))
    assert got["read_by"] == "2026-10-05T13:00:00Z"
    assert got["endpoints"] == {"api": s.API}
    assert board.get_stats()["escrow_held"] == str(s.REWARD + s.BOND)
    assert s.custody_holds(board, direct_vm)


def test_who_may_submit_and_how_often(board, direct_vm, direct_alice, direct_bob):
    bounty_id = s.funded(board, direct_vm, direct_alice)
    answer = s.submit(board, direct_vm, direct_alice, bounty_id)
    assert answer == "RETURNED: a sponsor does not submit to its own bounty"
    first = s.submit(board, direct_vm, direct_bob, bounty_id)
    answer = s.submit(board, direct_vm, direct_bob, bounty_id)
    assert answer.startswith("RETURNED: this bounty already has a submission of yours")
    direct_vm.warp(AFTER_WINDOW)
    board.lapse(first)
    second = s.submit(board, direct_vm, direct_bob, bounty_id)
    assert board.get_submission(second)["attempt"] == 2
    direct_vm.warp("2026-10-05T14:00:02Z")
    board.lapse(second)
    answer = s.submit(board, direct_vm, direct_bob, bounty_id)
    assert answer == "RETURNED: a developer may submit to one bounty 2 times"
    assert s.custody_holds(board, direct_vm)


def test_no_submission_after_the_deadline_or_to_a_bounty_that_is_not_funded(
        board, direct_vm, direct_alice, direct_bob):
    unfunded = s.created(board, direct_vm, direct_alice)
    answer = s.submit(board, direct_vm, direct_bob, unfunded, value=s.BOND)
    assert answer == "RETURNED: only a FUNDED bounty takes submissions"
    s.fund(board, direct_vm, direct_alice, unfunded)
    direct_vm.warp(AFTER_DEADLINE)
    answer = s.submit(board, direct_vm, direct_bob, unfunded)
    assert answer == "RETURNED: submissions closed at " + s.SUBMIT_BY
    assert s.custody_holds(board, direct_vm)

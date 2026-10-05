"""The contest window: who may ask for another reading, what a contest can and
cannot change, and what happens when a deliverable cannot be reached while it
is being questioned."""

from tests.direct import support as s

OTHER_API = "https://api.other.example.net/ferries"


def _state(board, submission_id):
    got = board.get_submission(submission_id)
    return (got["status"], got["verdict"], got["in_doubt"], got["window_ends"])


def _fix_and_break(vm, status_body=None, down=False):
    vm.clear_mocks()
    pages = {}
    if down:
        pages[s.API + s.STATUS_PATH] = s.DOWN
    elif status_body is not None:
        pages[s.API + s.STATUS_PATH] = status_body
    s.serve_all(vm, pages)


# -- the two contests -----------------------------------------------------------------

def test_a_developer_contests_a_rejection_and_the_new_reading_stands(
        board, direct_vm, direct_alice, direct_bob, direct_charlie):
    submission_id, first = s.evaluated(board, direct_vm, direct_alice, direct_bob,
                                       said_by_panel=s.answers(r5="NOT_MET"))
    assert _state(board, submission_id)[:2] == ("EVALUATED", "REJECTED")
    actions = board.get_actions(submission_id, s.NOW)
    assert actions["may_contest"] == {"developer": True, "sponsor": False}
    for other, message in ((direct_charlie, "only the developer or the sponsor contests"),
                           (direct_alice, "a sponsor contests an acceptance")):
        direct_vm.sender = other
        with direct_vm.expect_revert(message):
            board.contest(submission_id)
    s.panel(direct_vm, s.answers())
    direct_vm.warp("2026-10-05T12:30:00Z")
    direct_vm.sender = direct_bob
    record = s.record_of(board, board.contest(submission_id))
    assert (record["mode"], record["round"], record["outcome"], record["supersedes"]) == \
        ("CONTEST", 2, "ACCEPTED", first)
    # the window starts again, and now the acceptance is the sponsor's to question
    assert _state(board, submission_id) == ("EVALUATED", "ACCEPTED", False,
                                            "2026-10-05T13:30:00Z")
    actions = board.get_actions(submission_id, "2026-10-05T12:30:00Z")
    assert actions["may_contest"] == {"developer": False, "sponsor": True}
    with direct_vm.expect_revert("a developer contests a rejection"):
        board.contest(submission_id)
    direct_vm.warp("2026-10-05T13:30:01Z")
    assert board.finalize(submission_id) == "PAID"


def test_a_sponsor_contests_an_acceptance_and_a_reading_decides(
        board, direct_vm, direct_alice, direct_bob):
    submission_id, _first = s.evaluated(board, direct_vm, direct_alice, direct_bob)
    assert board.get_actions(submission_id, s.NOW)["may_contest"] == \
        {"developer": False, "sponsor": True}
    # upheld
    direct_vm.sender = direct_alice
    record = s.record_of(board, board.contest(submission_id))
    assert record["outcome"] == "ACCEPTED"
    with direct_vm.expect_revert("this party has contested this submission once already"):
        board.contest(submission_id)
    assert board.get_actions(submission_id, s.NOW)["may_contest"] == \
        {"developer": False, "sponsor": False}
    direct_vm.warp("2026-10-05T13:00:01Z")
    assert board.finalize(submission_id) == "PAID"


def test_a_contest_is_a_reading_not_a_veto(board, direct_vm, direct_alice, direct_bob):
    """The sponsor's contest finds the service no longer does what it did: the
    acceptance is replaced by a rejection - and the developer still has its own
    contest of that."""
    submission_id, _first = s.evaluated(board, direct_vm, direct_alice, direct_bob)
    _fix_and_break(direct_vm, status_body="{}")          # the key is gone
    s.panel(direct_vm, s.answers())
    direct_vm.sender = direct_alice
    record = s.record_of(board, board.contest(submission_id))
    assert record["outcome"] == "REJECTED" and record["states"]["r2"] == "NOT_MET"
    assert board.get_actions(submission_id, s.NOW)["may_contest"] == \
        {"developer": True, "sponsor": False}
    _fix_and_break(direct_vm)                            # the developer restores it
    s.panel(direct_vm, s.answers())
    direct_vm.sender = direct_bob
    assert s.record_of(board, board.contest(submission_id))["outcome"] == "ACCEPTED"
    # each has had its read contest: the verdict is what the last reading said
    assert board.get_actions(submission_id, s.NOW)["may_contest"] == \
        {"developer": False, "sponsor": False}
    direct_vm.warp("2026-10-05T13:00:01Z")
    assert board.finalize(submission_id) == "PAID"


def test_contest_rounds_are_counted_whatever_they_do(board, direct_vm, direct_alice,
                                                     direct_bob):
    submission_id, first = s.evaluated(board, direct_vm, direct_alice, direct_bob,
                                       said_by_panel=s.answers(r5="NOT_MET"))
    s.unusable(direct_vm)
    direct_vm.sender = direct_bob
    for minute in (10, 20):
        direct_vm.warp("2026-10-05T12:%02d:00Z" % minute)
        record = s.record_of(board, board.contest(submission_id))
        assert (record["applied"], record["outcome"]) == (False, "PANEL_UNUSABLE")
    got = board.get_submission(submission_id)
    # nothing changed but the window, which each round started again
    assert (got["verdict"], got["evaluation_id"], got["window_ends"]) == \
        ("REJECTED", first, "2026-10-05T13:20:00Z")
    with direct_vm.expect_revert("this party has used its 2 contest rounds"):
        board.contest(submission_id)
    assert board.get_actions(submission_id, "2026-10-05T12:20:00Z")["may_contest"] == \
        {"developer": False, "sponsor": False}
    direct_vm.warp("2026-10-05T13:20:01Z")
    assert board.finalize(submission_id) == "REJECTED"


def test_nothing_is_contested_outside_its_window(board, direct_vm, direct_alice, direct_bob):
    submission_id = s.submitted(board, direct_vm, direct_alice, direct_bob)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("only an EVALUATED submission is contested"):
        board.contest(submission_id)
    s.panel(direct_vm, s.answers())
    board.evaluate(submission_id)
    direct_vm.warp("2026-10-05T13:00:01Z")
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("the contest window closed at 2026-10-05T13:00:00Z"):
        board.contest(submission_id)
    board.finalize(submission_id)
    with direct_vm.expect_revert("only an EVALUATED submission is contested"):
        board.contest(submission_id)


# -- a deliverable that cannot be reached when it is questioned -------------------------

def test_an_acceptance_is_in_doubt_when_the_sponsors_contest_cannot_reach_the_deliverable(
        board, direct_vm, direct_alice, direct_bob, direct_charlie):
    submission_id, first = s.evaluated(board, direct_vm, direct_alice, direct_bob)
    _fix_and_break(direct_vm, down=True)
    direct_vm.warp("2026-10-05T12:40:00Z")
    direct_vm.sender = direct_alice
    record = s.record_of(board, board.contest(submission_id))
    assert (record["applied"], record["reason_code"]) == (False, "ENDPOINT_UNREACHABLE")
    assert _state(board, submission_id) == ("EVALUATED", "ACCEPTED", True,
                                            "2026-10-05T13:40:00Z")
    actions = board.get_actions(submission_id, "2026-10-05T12:40:00Z")
    assert actions["may_contest"] == {"developer": False, "sponsor": False}
    assert (actions["in_doubt"], actions["may_restore"]) == (True, True)
    # while it is in doubt nobody contests, and only the developer restores
    for who in (direct_alice, direct_bob):
        direct_vm.sender = who
        with direct_vm.expect_revert("the acceptance is in doubt"):
            board.contest(submission_id)
    for who in (direct_alice, direct_charlie):
        direct_vm.sender = who
        with direct_vm.expect_revert("only the developer restores its deliverable"):
            board.restore(submission_id)
    # the developer brings the service back: the reading is the verdict again
    _fix_and_break(direct_vm)
    s.panel(direct_vm, s.answers())
    direct_vm.warp("2026-10-05T12:50:00Z")
    direct_vm.sender = direct_bob
    record = s.record_of(board, board.restore(submission_id))
    assert (record["mode"], record["applied"], record["outcome"]) == \
        ("RESTORE", True, "ACCEPTED")
    assert _state(board, submission_id) == ("EVALUATED", "ACCEPTED", False,
                                            "2026-10-05T13:50:00Z")
    # the sponsor's contest has been answered by a reading: it has no second one
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("this party has contested this submission once already"):
        board.contest(submission_id)
    with direct_vm.expect_revert("only an acceptance in doubt is restored"):
        board.restore(submission_id)
    direct_vm.warp("2026-10-05T13:50:01Z")
    assert board.finalize(submission_id) == "PAID"
    assert s.custody_holds(board, direct_vm)


def test_an_acceptance_still_in_doubt_when_the_window_ends_is_rejected(
        board, direct_vm, direct_alice, direct_bob):
    submission_id, _first = s.evaluated(board, direct_vm, direct_alice, direct_bob)
    direct_vm.clear_mocks()
    s.serve_all(direct_vm, {s.DOCS_URL: s.DOWN})         # this time it is the document
    direct_vm.sender = direct_alice
    record = s.record_of(board, board.contest(submission_id))
    assert record["reason_code"] == "EVIDENCE_UNREADABLE"
    assert board.get_submission(submission_id)["in_doubt"] is True
    direct_vm.warp("2026-10-05T13:00:01Z")
    assert board.get_actions(submission_id, "2026-10-05T13:00:01Z")["may_restore"] is False
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("the contest window closed at 2026-10-05T13:00:00Z"):
        board.restore(submission_id)
    assert board.finalize(submission_id) == "REJECTED"
    got = board.get_submission(submission_id)
    assert (got["result"], got["result_reason"], got["verdict"], got["evaluation_id"]) == \
        ("REJECTED", "DELIVERABLE_GONE_WHILE_CONTESTED", "REJECTED", "")
    assert got["in_doubt"] is False and got["bond_fate"] == "FORFEITED_TO_SPONSOR"
    assert board.get_outcome("BNT-000001")["open"] is True
    assert s.balance(board, direct_alice) == s.BOND
    assert s.custody_holds(board, direct_vm)


def test_only_a_round_that_reaches_the_deliverable_clears_a_doubt(
        board, direct_vm, direct_alice, direct_bob):
    submission_id, _first = s.evaluated(board, direct_vm, direct_alice, direct_bob)
    _fix_and_break(direct_vm, down=True)
    direct_vm.sender = direct_alice
    board.contest(submission_id)
    direct_vm.sender = direct_bob
    direct_vm.warp("2026-10-05T12:10:00Z")
    board.restore(submission_id)                         # still down
    assert _state(board, submission_id) == ("EVALUATED", "ACCEPTED", True,
                                            "2026-10-05T13:10:00Z")
    _fix_and_break(direct_vm)
    s.panel(direct_vm, s.answers(r5="NOT_MET"))
    direct_vm.warp("2026-10-05T12:30:00Z")
    record = s.record_of(board, board.restore(submission_id))   # read: and it says no
    assert record["outcome"] == "REJECTED"
    assert _state(board, submission_id) == ("EVALUATED", "REJECTED", False,
                                            "2026-10-05T13:30:00Z")
    with direct_vm.expect_revert("only an acceptance in doubt is restored"):
        board.restore(submission_id)
    # a rejection is the developer's to contest, once
    assert board.get_actions(submission_id, "2026-10-05T12:30:00Z")["may_contest"] == \
        {"developer": True, "sponsor": False}


def test_a_deliverable_that_is_back_is_no_longer_in_doubt_whatever_the_model_says(
        board, direct_vm, direct_alice, direct_bob):
    """The restore round reached every document and every call was answered;
    the model gave nothing usable. The doubt was about whether the deliverable
    is there, and it is: the acceptance stands as it was, and the sponsor's
    contest - which no round has read yet - is still the sponsor's to ask."""
    submission_id, first = s.evaluated(board, direct_vm, direct_alice, direct_bob)
    _fix_and_break(direct_vm, down=True)
    direct_vm.sender = direct_alice
    board.contest(submission_id)
    _fix_and_break(direct_vm)
    s.unusable(direct_vm)
    direct_vm.warp("2026-10-05T12:20:00Z")
    direct_vm.sender = direct_bob
    record = s.record_of(board, board.restore(submission_id))
    assert (record["outcome"], record["applied"]) == ("PANEL_UNUSABLE", False)
    assert [p["result"] for p in record["probes"]] == ["ANSWERED"] * 3
    got = board.get_submission(submission_id)
    assert (got["in_doubt"], got["verdict"], got["evaluation_id"]) == \
        (False, "ACCEPTED", first)
    actions = board.get_actions(submission_id, "2026-10-05T12:20:00Z")
    assert actions["may_restore"] is False
    assert actions["may_contest"] == {"developer": False, "sponsor": True}
    s.panel(direct_vm, s.answers())
    direct_vm.sender = direct_alice
    assert s.record_of(board, board.contest(submission_id))["outcome"] == "ACCEPTED"
    with direct_vm.expect_revert("this party has contested this submission once already"):
        board.contest(submission_id)
    direct_vm.warp("2026-10-05T13:20:01Z")
    assert board.finalize(submission_id) == "PAID"


def test_restore_rounds_are_capped(board, direct_vm, direct_alice, direct_bob):
    submission_id, _first = s.evaluated(board, direct_vm, direct_alice, direct_bob)
    _fix_and_break(direct_vm, down=True)
    direct_vm.sender = direct_alice
    board.contest(submission_id)
    direct_vm.sender = direct_bob
    for _ in range(3):
        board.restore(submission_id)
    assert board.get_actions(submission_id, s.NOW)["may_restore"] is False
    with direct_vm.expect_revert("has used its 3 restore rounds"):
        board.restore(submission_id)
    assert board.get_submission(submission_id)["rounds"] == 5


def test_each_doubt_has_its_own_restore_rounds(board, direct_vm, direct_alice, direct_bob):
    """Two restore rounds still cannot reach the deliverable; a third reaches it
    and the model says nothing usable, which clears the doubt and leaves the
    sponsor its contest. The sponsor contests again at another bad moment: the
    developer answers that doubt with three rounds of its own."""
    submission_id, _first = s.evaluated(board, direct_vm, direct_alice, direct_bob)
    _fix_and_break(direct_vm, down=True)
    direct_vm.sender = direct_alice
    board.contest(submission_id)
    direct_vm.sender = direct_bob
    board.restore(submission_id)
    board.restore(submission_id)
    _fix_and_break(direct_vm)
    s.unusable(direct_vm)
    board.restore(submission_id)
    assert board.get_submission(submission_id)["in_doubt"] is False
    _fix_and_break(direct_vm, down=True)
    direct_vm.sender = direct_alice
    board.contest(submission_id)                         # the sponsor's second and last round
    actions = board.get_actions(submission_id, s.NOW)
    assert (actions["in_doubt"], actions["may_restore"]) == (True, True)
    _fix_and_break(direct_vm)
    s.panel(direct_vm, s.answers())
    direct_vm.sender = direct_bob
    record = s.record_of(board, board.restore(submission_id))
    assert record["outcome"] == "ACCEPTED"
    assert board.get_submission(submission_id)["in_doubt"] is False


def test_a_developers_own_contest_that_reaches_nothing_raises_no_doubt(
        board, direct_vm, direct_alice, direct_bob):
    submission_id, _first = s.evaluated(board, direct_vm, direct_alice, direct_bob,
                                        said_by_panel=s.answers(r5="NOT_MET"))
    _fix_and_break(direct_vm, down=True)
    direct_vm.sender = direct_bob
    record = s.record_of(board, board.contest(submission_id))
    assert record["reason_code"] == "ENDPOINT_UNREACHABLE"
    got = board.get_submission(submission_id)
    assert (got["verdict"], got["in_doubt"]) == ("REJECTED", False)


def test_a_sponsors_contest_with_an_unusable_answer_raises_no_doubt(
        board, direct_vm, direct_alice, direct_bob):
    submission_id, first = s.evaluated(board, direct_vm, direct_alice, direct_bob)
    s.unusable(direct_vm)
    direct_vm.sender = direct_alice
    record = s.record_of(board, board.contest(submission_id))
    assert record["outcome"] == "PANEL_UNUSABLE"
    got = board.get_submission(submission_id)
    assert (got["verdict"], got["in_doubt"], got["evaluation_id"]) == \
        ("ACCEPTED", False, first)
    # the sponsor's contest was not read: it may ask once more
    assert board.get_actions(submission_id, s.NOW)["may_contest"]["sponsor"] is True
    s.panel(direct_vm, s.answers())
    board.contest(submission_id)
    with direct_vm.expect_revert("this party has contested this submission once already"):
        board.contest(submission_id)


# -- the queue and the contest window --------------------------------------------------

def test_an_earlier_acceptance_in_doubt_is_first_until_it_is_finalized(
        board, direct_vm, direct_alice, direct_bob, direct_charlie):
    first, _r = s.evaluated(board, direct_vm, direct_alice, direct_bob)
    second = s.submit(board, direct_vm, direct_charlie, "BNT-000001",
                      payload=s.submission(api=OTHER_API))
    s.serve(direct_vm, OTHER_API + s.STATUS_PATH, s.STATUS_BODY)
    s.serve(direct_vm, OTHER_API + s.MISSING_PATH, "gone", 404)
    direct_vm.sender = direct_charlie
    board.evaluate(second)
    # the first goes dark under the sponsor's contest
    direct_vm.clear_mocks()
    s.serve_all(direct_vm, {s.API + s.STATUS_PATH: s.DOWN})
    direct_vm.warp("2026-10-05T12:30:00Z")
    direct_vm.sender = direct_alice
    board.contest(first)
    direct_vm.warp("2026-10-05T13:00:01Z")               # the second's window has passed
    with direct_vm.expect_revert("first in line: " + first):
        board.finalize(second)
    assert board.get_actions(second, "2026-10-05T13:00:01Z")["waits_for"] == first
    # once the first's window has passed in doubt it can only be rejected: it no
    # longer holds its place, and nobody has to finalize it first
    direct_vm.warp("2026-10-05T13:30:01Z")
    actions = board.get_actions(second, "2026-10-05T13:30:01Z")
    assert (actions["waits_for"], actions["may_finalize"]) == ("", True)
    assert board.finalize(second) == "PAID"
    got = board.get_submission(first)
    assert (got["result"], got["result_reason"]) == \
        ("REJECTED", "DELIVERABLE_GONE_WHILE_CONTESTED")
    assert board.get_outcome("BNT-000001")["winner"] == s.hexaddr(direct_charlie)
    assert s.balance(board, direct_alice) == s.BOND      # the first one's bond
    assert s.balance(board, direct_charlie) == s.REWARD + s.BOND
    assert s.custody_holds(board, direct_vm)


def test_an_earlier_rejection_is_first_only_while_it_can_be_contested(
        board, direct_vm, direct_alice, direct_bob, direct_charlie):
    first, _r = s.evaluated(board, direct_vm, direct_alice, direct_bob,
                            said_by_panel=s.answers(r5="NOT_MET"))
    direct_vm.warp("2026-10-05T12:20:00Z")
    second = s.submit(board, direct_vm, direct_charlie, "BNT-000001")
    s.panel(direct_vm, s.answers())
    direct_vm.sender = direct_charlie
    board.evaluate(second)
    # the developer of the first contests late: its window now outlasts the second's
    s.unusable(direct_vm)
    direct_vm.warp("2026-10-05T12:55:00Z")
    direct_vm.sender = direct_bob
    board.contest(first)
    direct_vm.warp("2026-10-05T13:20:01Z")
    with direct_vm.expect_revert("first in line: " + first):
        board.finalize(second)
    # once the first's window has passed it can no longer be accepted: nobody has
    # to finalize it for the second to be paid
    direct_vm.warp("2026-10-05T13:55:01Z")
    assert board.finalize(second) == "PAID"
    got = board.get_submission(first)
    assert (got["result"], got["result_reason"], got["bond_fate"]) == \
        ("REJECTED", "EVALUATED", "FORFEITED_TO_SPONSOR")
    assert s.custody_holds(board, direct_vm)


def test_a_rejection_its_developer_can_no_longer_contest_holds_no_place(
        board, direct_vm, direct_alice, direct_bob, direct_charlie):
    first, _r = s.evaluated(board, direct_vm, direct_alice, direct_bob,
                            said_by_panel=s.answers(r5="NOT_MET"))
    direct_vm.sender = direct_bob
    board.contest(first)                                 # read again: rejected again
    second = s.submit(board, direct_vm, direct_charlie, "BNT-000001")
    s.panel(direct_vm, s.answers())
    direct_vm.sender = direct_charlie
    board.evaluate(second)
    direct_vm.warp("2026-10-05T12:30:00Z")
    direct_vm.sender = direct_alice
    board.contest(second)                                # upheld; its window now ends later
    direct_vm.warp("2026-10-05T12:59:00Z")
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("this party has contested this submission once already"):
        board.contest(first)
    direct_vm.warp("2026-10-05T13:30:01Z")
    assert board.finalize(second) == "PAID"


def test_a_rejection_its_developer_can_still_contest_holds_its_place(
        board, direct_vm, direct_alice, direct_bob, direct_charlie):
    first = s.submitted(board, direct_vm, direct_alice, direct_bob)
    second = s.submit(board, direct_vm, direct_charlie, "BNT-000001")
    s.panel(direct_vm, s.answers())
    direct_vm.sender = direct_charlie
    board.evaluate(second)
    s.panel(direct_vm, s.answers(r5="NOT_MET"))
    direct_vm.warp("2026-10-05T12:40:00Z")
    direct_vm.sender = direct_bob
    board.evaluate(first)                                # rejected; its window ends 13:40
    direct_vm.warp("2026-10-05T13:00:01Z")
    with direct_vm.expect_revert("first in line: " + first):
        board.finalize(second)
    # used up, its contest rounds no longer hold anything
    s.unusable(direct_vm)
    board.contest(first)
    board.contest(first)
    assert board.get_actions(second, "2026-10-05T13:00:01Z")["waits_for"] == ""
    assert board.finalize(second) == "PAID"


def test_a_rejection_that_can_no_longer_be_contested_ends_rejected_with_the_bounty(
        board, direct_vm, direct_alice, direct_bob, direct_charlie):
    """Rejected, contested, rejected again - and still inside the window its own
    contest restarted when a later submission is paid. It held no place, so it
    ends as it was going to end, and its bond is forfeit: paying somebody else
    is not a way to get a rejected bond back."""
    first, _r = s.evaluated(board, direct_vm, direct_alice, direct_bob,
                            said_by_panel=s.answers(r5="NOT_MET"))
    second = s.submit(board, direct_vm, direct_charlie, "BNT-000001")
    s.panel(direct_vm, s.answers())
    direct_vm.sender = direct_charlie
    board.evaluate(second)
    s.panel(direct_vm, s.answers(r5="NOT_MET"))
    direct_vm.warp("2026-10-05T12:59:00Z")
    direct_vm.sender = direct_bob
    board.contest(first)                                 # its window now ends 13:59
    direct_vm.warp("2026-10-05T13:00:01Z")
    assert board.finalize(second) == "PAID"
    got = board.get_submission(first)
    assert (got["result"], got["result_reason"], got["bond_fate"]) == \
        ("REJECTED", "EVALUATED", "FORFEITED_TO_SPONSOR")
    assert s.balance(board, direct_bob) == 0 and s.balance(board, direct_alice) == s.BOND
    assert s.custody_holds(board, direct_vm)


def test_an_acceptance_in_doubt_with_no_restore_round_left_holds_no_place(
        board, direct_vm, direct_alice, direct_bob, direct_charlie):
    first, _r = s.evaluated(board, direct_vm, direct_alice, direct_bob,
                            contest_window=7200)
    second = s.submit(board, direct_vm, direct_charlie, "BNT-000001",
                      payload=s.submission(api=OTHER_API))
    s.serve(direct_vm, OTHER_API + s.STATUS_PATH, s.STATUS_BODY)
    s.serve(direct_vm, OTHER_API + s.MISSING_PATH, "gone", 404)
    direct_vm.sender = direct_charlie
    board.evaluate(second)                               # accepted; its window ends 14:00
    direct_vm.clear_mocks()
    s.serve_all(direct_vm, {s.API + s.STATUS_PATH: s.DOWN})
    direct_vm.warp("2026-10-05T13:30:00Z")
    direct_vm.sender = direct_alice
    board.contest(first)                                 # in doubt; its window ends 15:30
    direct_vm.sender = direct_bob
    board.restore(first)
    board.restore(first)
    direct_vm.warp("2026-10-05T14:00:01Z")
    with direct_vm.expect_revert("first in line: " + first):
        board.finalize(second)                           # one restore round is left
    board.restore(first)                                 # the last, and still not there
    assert board.get_actions(second, "2026-10-05T14:00:01Z")["waits_for"] == ""
    assert board.finalize(second) == "PAID"
    got = board.get_submission(first)
    assert (got["result"], got["result_reason"], got["bond_fate"]) == \
        ("REJECTED", "DELIVERABLE_GONE_WHILE_CONTESTED", "FORFEITED_TO_SPONSOR")

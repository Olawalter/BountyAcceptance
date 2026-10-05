"""Rules that stand behind another rule. A forged payload usually trips more than
one check; each test here holds everything else still and breaks one rule, so
that removing that rule is noticed even where a second one would have caught
the same forgery."""

import json

from tests.direct import support as s

LATER = "2026-10-05T13:00:01Z"


def _honest(board, mod, vm, sponsor, developer, **kwargs):
    """A decided round, as the leader reported it and as a validator would have
    it in hand: (submission id, payload, ctx, docs, answers)."""
    submission_id, _evaluation_id = s.evaluated(board, vm, sponsor, developer, **kwargs)
    payload = s.leader_payload(vm)
    ctx = board._ctx(board.submissions.get(submission_id), "EVALUATE", s.NOW)
    ctx["round"] = 1
    body = (kwargs.get("pages") or {}).get(s.DOCS_URL, s.DOCS)
    _source, doc = mod._document(ctx["items"][0], 0, 200, "text/html", body.encode("utf-8"))
    status = (kwargs.get("pages") or {}).get(s.API + s.STATUS_PATH, s.STATUS_BODY)
    answers = {"r1": {"status": 200, "text": status}, "r2": {"status": 200, "text": status},
               "r3": {"status": 404, "text": "not found"}}
    return (submission_id, payload, ctx, {"docs": doc}, answers)


def _gate(mod, payload, ctx, docs=None, answers=None):
    return mod._parse_payload(json.dumps(payload), ctx, docs, answers)


def _changed(payload, change):
    forged = json.loads(json.dumps(payload))
    change(forged)
    return forged


# -- the payload as a whole ---------------------------------------------------------

def test_an_honest_payload_passes_its_own_gate(board, mod, direct_vm, direct_alice,
                                               direct_bob):
    _sid, payload, ctx, docs, answers = _honest(board, mod, direct_vm, direct_alice,
                                                direct_bob)
    assert _gate(mod, payload, ctx) is not None
    assert _gate(mod, payload, ctx, docs, answers) is not None


def test_each_part_of_a_payload_is_gated_on_its_own(board, mod, direct_vm, direct_alice,
                                                    direct_bob):
    _sid, payload, ctx, docs, answers = _honest(board, mod, direct_vm, direct_alice,
                                                direct_bob)

    def refused(change, with_own=False):
        forged = _changed(payload, change)
        return (_gate(mod, forged, ctx, docs, answers) if with_own
                else _gate(mod, forged, ctx)) is None

    # one source for each document, and no more
    assert refused(lambda p: p["sources"].append(dict(p["sources"][0])))
    # markers are strings, and a marker that is not is refused without a crash
    assert refused(lambda p: p.update(markers=[1, "docs:BODY"]))
    # a title that could not be stored
    assert refused(lambda p: p["sources"][0].update(title="Quayside" + chr(0xDC00)))
    # the reason is recomputed from what the payload itself reports
    def code_decided(p):
        p["panel_reason"] = "DOCUMENT_ADDRESSES_EVALUATOR"
        p["panel_state"] = "SKIPPED"
        s.finding_in(p, "r5").update(by="CODE", state="NOT_MET", quotes=[], note="")
    assert refused(code_decided)
    # ... also when nothing else in the payload gives the false reason away
    assert refused(lambda p: p.update(panel_reason="NOTHING_TO_READ"))
    # a status on record cannot contradict the state of a body check
    assert refused(lambda p: p["probes"][1].update(http_status=204))
    assert not refused(lambda p: p["probes"][1].update(byte_count=7))
    # with this node's own documents and answers in hand: a check is recomputed
    assert refused(lambda p: s.finding_in(p, "r4").update(state="NOT_MET"), with_own=True)
    assert not refused(lambda p: s.finding_in(p, "r4").update(state="NOT_MET"))
    # and a quote must be in the text this node read
    def other_passage(p):
        s.finding_in(p, "r5")["quotes"] = [
            {"evidence_id": "docs", "text": "Keys never expire and need no header."}]
    assert refused(other_passage, with_own=True)
    assert not refused(other_passage)


def test_a_round_that_decides_nothing_is_gated_too(board, mod, direct_vm, direct_alice,
                                                   direct_bob):
    submission_id = s.submitted(board, direct_vm, direct_alice, direct_bob,
                                pages={s.API + s.STATUS_PATH: s.DOWN})
    direct_vm.sender = direct_bob
    board.evaluate(submission_id)
    payload = s.leader_payload(direct_vm)
    ctx = board._ctx(board.submissions.get(submission_id), "EVALUATE", s.NOW)
    ctx["round"] = 1
    assert payload["panel_reason"] == "ENDPOINT_UNREACHABLE" and payload["findings"] == []
    assert _gate(mod, payload, ctx) is not None
    # it reports no findings
    told = _changed(payload, lambda p: p["findings"].append(
        {"id": "r1", "by": "CODE", "state": "MET", "quotes": [], "note": ""}))
    assert _gate(mod, told, ctx) is None
    # the documents were read, so the calls were made: "not asked" is not an answer
    def not_asked(p):
        for probe in p["probes"]:
            probe.update(result="NOT_ASKED", http_status=0, byte_count=0)
    assert _gate(mod, _changed(payload, not_asked), ctx) is None


# -- the calls a payload reports ------------------------------------------------------

def test_what_a_payload_says_of_each_call_is_gated(mod, board):
    ctx = {"bounty": s.bounty(), "endpoints": {"api": s.API}}
    needed = mod._probes_needed(ctx)
    assert [n["requirement"] for n in needed] == ["r1", "r2", "r3"]

    def probes(**changes):
        out = [{"requirement": n["requirement"], "url": n["url"], "result": "ANSWERED",
                "http_status": 200, "byte_count": 10} for n in needed]
        out[0].update(changes)
        return out

    assert mod._valid_probes(probes(), ctx) is True
    assert mod._valid_probes(probes(result="FINE"), ctx) is False
    assert mod._valid_probes(probes(result=None), ctx) is False
    # an answer has an answer's status
    for status in (503, 500, 99, 429, 408, 425):
        assert mod._valid_probes(probes(http_status=status), ctx) is False, status
    assert mod._valid_probes(probes(http_status=404), ctx) is True
    assert mod._valid_probes(probes(result="UNREACHABLE", http_status=503), ctx) is True
    # a call that was not made has neither status nor size
    unasked = [dict(p, result="NOT_ASKED", http_status=0, byte_count=0) for p in probes()]
    assert mod._valid_probes(unasked, ctx) is True
    assert mod._valid_probes([dict(p, http_status=200) for p in unasked], ctx) is False
    assert mod._valid_probes([dict(p, byte_count=1) for p in unasked], ctx) is False
    # and either every call was made or none
    mixed = probes()
    mixed[2] = dict(mixed[2], result="NOT_ASKED", http_status=0, byte_count=0)
    assert mod._valid_probes(mixed, ctx) is False
    assert mod._valid_probes(probes(byte_count=10 ** 9), ctx) is True
    assert mod._valid_probes(probes(byte_count=10 ** 9 + 1), ctx) is False
    assert mod._valid_probes(probes(byte_count=-1), ctx) is False
    assert mod._valid_probes(probes(http_status="200"), ctx) is False
    assert mod._valid_probes(probes(url=s.API + "/v1/other"), ctx) is False


# -- one finding ---------------------------------------------------------------------

def test_each_rule_for_a_finding_refuses_by_itself(mod, board):
    check = {"id": "r4", "kind": "CHECK"}
    prose = {"id": "r5", "kind": "JUDGED"}
    tokens = mod._panel_tokens({"docs": s.AUTH_LINE})

    def finding(requirement, **fields):
        base = {"id": requirement["id"], "by": "CODE", "state": "MET", "quotes": [],
                "note": ""}
        base.update(fields)
        return base

    def valid(f, requirement, by_code=False, check_state=None, with_tokens=None):
        return mod._valid_finding(f, requirement, ["docs"], with_tokens, by_code, check_state)

    quote = {"evidence_id": "docs", "text": "Every request carries an API key"}
    # a check is what this node computed, and nothing else
    assert valid(finding(check), check, check_state="MET") is True
    assert valid(finding(check), check, check_state="NOT_MET") is False
    assert valid(finding(check, by="PANEL"), check) is False
    assert valid(finding(check, quotes=[quote]), check) is False
    # where code decided, no reading is accepted
    assert valid(finding(prose, state="NOT_MET"), prose, by_code=True) is True
    assert valid(finding(prose, by="PANEL", state="NOT_MET"), prose, by_code=True) is False
    assert valid(finding(prose, state="MET"), prose, by_code=True) is False
    # the panel's finding: a MET quotes, and only a MET does
    met = finding(prose, by="PANEL", quotes=[quote])
    assert valid(met, prose) is True
    assert valid(finding(prose, by="PANEL"), prose) is False
    assert valid(finding(prose, by="PANEL", state="NOT_MET", quotes=[quote]), prose) is False
    assert valid(finding(prose, by="PANEL", state="NOT_MET"), prose) is True
    assert valid(finding(prose, by="CODE", quotes=[quote]), prose) is False
    # a quote is in this node's text, and reaches the record clean
    assert valid(met, prose, with_tokens=tokens) is True
    absent = {"evidence_id": "docs", "text": "Keys never expire at all"}
    assert valid(finding(prose, by="PANEL", quotes=[absent]), prose, with_tokens=tokens) \
        is False
    for dirty in ("Every request carries" + chr(0x200D) + " an API key",
                  "Every request carries" + chr(7) + " an API key",
                  "Every request carries" + chr(0x2028) + "an API key"):
        unclean = {"evidence_id": "docs", "text": dirty}
        assert valid(finding(prose, by="PANEL", quotes=[unclean]), prose) is False, \
            ascii(dirty)
    # a state is one of the two
    for state in ("UNCLEAR", "", "met", None, 1):
        assert valid(finding(prose, by="PANEL", state=state), prose) is False, state


def test_a_quote_joined_from_two_places_is_dropped(mod, board):
    text = "Keys expire... ask the operator for a new key when one does."
    tokens = mod._panel_tokens({"docs": text})
    requirement = {"id": "r5", "kind": "JUDGED"}
    spliced = mod._normalize_finding(
        requirement, {"state": "MET", "quotes": [{"evidence_id": "docs",
                                                 "text": "Keys expire... ask the operator"}]},
        ["docs"], tokens)
    assert (spliced["state"], spliced["quotes"]) == ("NOT_MET", [])
    whole = mod._normalize_finding(
        requirement, {"state": "MET", "quotes": [{"evidence_id": "docs",
                                                 "text": "ask the operator for a new key"}]},
        ["docs"], tokens)
    assert whole["state"] == "MET"


# -- one source ----------------------------------------------------------------------

def test_what_a_payload_says_of_each_document_is_gated(board, mod, direct_vm, direct_alice,
                                                       direct_bob):
    _sid, payload, ctx, _docs, _answers = _honest(board, mod, direct_vm, direct_alice,
                                                  direct_bob)
    item = ctx["items"][0]
    read = payload["sources"][0]
    assert mod._valid_source(read, item) is True
    # the address that answered is one of the document's
    assert mod._valid_source(dict(read, via=1), item) is False
    assert mod._valid_source(dict(read, via=-1), item) is False
    # a document that was not fetched carries nothing
    unfetched = mod._empty_source("docs", "SERVER_ERROR", 503, "text/plain", 0)
    assert mod._valid_source(unfetched, item) is True
    for key, value in (("title", "Quayside"), ("raw_sha256", item["sha256"]),
                       ("content_digest", "0" * 64), ("truncated", True), ("via", 0)):
        assert mod._valid_source(dict(unfetched, **{key: value}), item) is False, key


def test_text_that_takes_no_space_is_not_part_of_a_document(mod, board):
    item = {"evidence_id": "docs", "sha256": "0" * 64, "urls": [s.DOCS_URL]}
    # a letter that renders as a gap is the gap; a control character is nothing;
    # a joiner joins nothing
    hidden = ("Every request car" + chr(1) + "ries an API" + chr(0x3164) + "key"
              + chr(0x200D) * 3 + ".")
    _source, doc = mod._document(item, 0, 200, "text/plain", hidden.encode("utf-8"))
    assert doc["text"] == "Every request carries an API key."
    assert mod._count_words(doc["text"]) == 6
    _source, plain = mod._document(item, 0, 200, "text/plain",
                                   "Every request carries an API key.".encode("utf-8"))
    assert _source["content_digest"] == mod._sha256_hex(doc["text"])
    assert plain["text"] == doc["text"]


def test_a_status_that_is_not_one_is_recorded_as_none(board, mod, direct_vm):
    s.serve(direct_vm, s.DOCS_URL, s.DOCS, status=1000)
    failure, code, _content_type, body = mod._fetch_bytes(s.DOCS_URL)
    assert (code, body) == (0, None) and failure != ""
    direct_vm.clear_mocks()
    s.serve(direct_vm, s.DOCS_URL, s.DOCS, status=200)
    assert mod._fetch_bytes(s.DOCS_URL)[1] == 200


# -- what nodes compare ----------------------------------------------------------------

def test_each_thing_nodes_compare_is_compared(board, mod, direct_vm, direct_alice,
                                              direct_bob):
    _sid, payload, ctx, _docs, _answers = _honest(board, mod, direct_vm, direct_alice,
                                                  direct_bob)
    assert mod._evidence_difference(ctx, payload, payload) == ""
    for change, said in (
            (lambda p: p.update(panel_reason="NOTHING_TO_READ"), "reason"),
            (lambda p: p.update(markers=["docs:BODY"]), "markers"),
            (lambda p: p["probes"][2].update(result="UNREACHABLE"), "probe r3"),
            (lambda p: p["sources"][0].update(status="PARTIAL"), "docs status"),
            (lambda p: p["sources"][0].update(truncated=True), "docs truncated"),
            (lambda p: p["sources"][0].update(byte_count=1), "docs byte_count"),
            (lambda p: p["sources"][0].update(content_digest="0" * 64),
             "docs content_digest"),
            (lambda p: p["sources"][0].update(raw_sha256="0" * 64), "docs raw_sha256"),
            (lambda p: p["sources"][0].update(title="Another title"), "docs title")):
        difference = mod._evidence_difference(ctx, payload, _changed(payload, change))
        assert difference.startswith(said), (said, difference)
    # how a document came, and the status and size of an answer, are not compared
    for change in (lambda p: p["sources"][0].update(http_status=206),
                   lambda p: p["sources"][0].update(content_type="text/plain"),
                   lambda p: p["probes"][0].update(byte_count=1),
                   lambda p: p["probes"][0].update(http_status=201)):
        assert mod._evidence_difference(ctx, payload, _changed(payload, change)) == ""


def test_a_round_that_decides_nothing_is_agreed_by_its_reason_alone(mod, board):
    ctx = {"items": []}
    mine = {"panel_reason": "EVIDENCE_UNREADABLE", "markers": [], "probes": []}
    theirs = {"panel_reason": "EVIDENCE_UNREADABLE", "markers": ["docs:BODY"], "probes": []}
    assert mod._evidence_difference(ctx, mine, theirs) == ""
    for other in ("", "ENDPOINT_UNREACHABLE", "DOCUMENT_ADDRESSES_EVALUATOR"):
        assert mod._evidence_difference(ctx, mine, dict(theirs, panel_reason=other)) != ""


def test_a_validator_refuses_evidence_that_differs_even_when_the_verdict_agrees(
        board, direct_vm, direct_alice, direct_bob):
    s.evaluated(board, direct_vm, direct_alice, direct_bob)
    told = s.leader_payload(direct_vm)
    told["sources"][0]["title"] = "A title the page does not have"
    assert s.replay(direct_vm, told) is False


def test_the_ratified_payload_is_gated_again_before_anything_is_stored(
        board, mod, direct_vm, direct_alice, direct_bob):
    submission_id = s.submitted(board, direct_vm, direct_alice, direct_bob)
    s.panel(direct_vm, s.answers())
    original = mod._parse_payload
    mod._parse_payload = lambda *args, **kwargs: None
    try:
        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("the ratified payload failed the gate"):
            board.evaluate(submission_id)
    finally:
        mod._parse_payload = original
    assert board.get_submission(submission_id)["rounds"] == 0


# -- records and state ----------------------------------------------------------------

def test_a_round_that_decides_nothing_records_only_what_was_agreed(
        board, direct_vm, direct_alice, direct_bob):
    planted = s.page("Quayside timetable API",
                     [s.FILLER, s.AUTH_LINE, s.REVOKE_LINE, s.INJECTION])
    submission_id = s.submitted(board, direct_vm, direct_alice, direct_bob,
                                pages={s.DOCS_URL: planted,
                                       s.API + s.STATUS_PATH: s.DOWN})
    direct_vm.sender = direct_bob
    record = s.record_of(board, board.evaluate(submission_id))
    assert record["reason_code"] == "ENDPOINT_UNREACHABLE" and record["applied"] is False
    # the leader saw the planted line; a round nobody compared end to end stores none of it
    assert s.leader_payload(direct_vm)["markers"] == ["docs:BODY"]
    assert record["markers"] == []
    assert [sorted(x.keys()) for x in record["sources"]] == \
        [["declared_sha256", "evidence_id", "label", "status"]]
    assert "sources.status and probes of a round that could not reach the deliverable" \
        in record["leader_chosen"]


def test_a_verdict_carries_the_time_of_the_round_it_rests_on(board, direct_vm, direct_alice,
                                                            direct_bob):
    submission_id, _first = s.evaluated(board, direct_vm, direct_alice, direct_bob)
    assert board.get_submission(submission_id)["read_at"] == s.NOW
    direct_vm.warp("2026-10-05T12:20:00Z")
    direct_vm.sender = direct_alice
    board.contest(submission_id)                         # read again
    assert board.get_submission(submission_id)["read_at"] == "2026-10-05T12:20:00Z"
    direct_vm.clear_mocks()
    s.serve_all(direct_vm, {s.DOCS_URL: s.DOWN})
    s.unusable(direct_vm)
    direct_vm.sender = direct_bob
    # (the sponsor has had its read contest; nothing more is read)
    direct_vm.warp("2026-10-05T13:20:01Z")
    assert board.finalize(submission_id) == "PAID"
    assert board.get_submission(submission_id)["read_at"] == "2026-10-05T12:20:00Z"


def test_a_verdict_that_rests_on_no_round_carries_no_reading_time(
        board, direct_vm, direct_alice, direct_bob):
    submission_id, _first = s.evaluated(board, direct_vm, direct_alice, direct_bob)
    direct_vm.clear_mocks()
    s.serve_all(direct_vm, {s.DOCS_URL: s.DOWN})
    direct_vm.sender = direct_alice
    board.contest(submission_id)                         # in doubt
    direct_vm.warp(LATER)
    assert board.finalize(submission_id) == "REJECTED"
    got = board.get_submission(submission_id)
    assert (got["result_reason"], got["read_at"], got["states"], got["reason_code"]) == \
        ("DELIVERABLE_GONE_WHILE_CONTESTED", "", {}, "")


# -- guards on the writes --------------------------------------------------------------

def test_a_funding_above_the_reward_goes_back(board, direct_vm, direct_alice):
    bounty_id = s.created(board, direct_vm, direct_alice)
    answer = s.fund(board, direct_vm, direct_alice, bounty_id, value=s.REWARD + 1)
    assert answer.startswith("RETURNED: the value sent must be exactly the reward")
    assert board.get_bounty(bounty_id)["status"] == "CREATED"
    assert s.custody_holds(board, direct_vm) and direct_vm.bank.contract == 0


def test_a_document_has_at_most_three_addresses_and_none_once_final(
        board, direct_vm, direct_alice, direct_bob):
    submission_id = s.submitted(board, direct_vm, direct_alice, direct_bob)
    direct_vm.sender = direct_bob
    board.add_mirror(submission_id, "docs", s.DOCS_MIRROR)
    board.add_mirror(submission_id, "docs", "https://files.example.net/quayside/copy.html")
    assert len(board.get_submission(submission_id)["documents"][0]["urls"]) == 3
    with direct_vm.expect_revert("a document has at most 3 addresses"):
        board.add_mirror(submission_id, "docs", "https://files.example.net/quayside/4.html")
    direct_vm.warp(LATER)
    board.lapse(submission_id)
    with direct_vm.expect_revert("an address is added to a submission that is not yet final"):
        board.add_mirror(submission_id, "docs", "https://files.example.net/quayside/5.html")


def test_each_write_acts_only_on_the_status_it_is_for(board, direct_vm, direct_alice,
                                                      direct_bob):
    submission_id, _first = s.evaluated(board, direct_vm, direct_alice, direct_bob,
                                        evaluate_window=3600, contest_window=7200)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("only a SUBMITTED submission is evaluated"):
        board.evaluate(submission_id)
    direct_vm.warp(LATER)                                # past its read-by time, still evaluated
    with direct_vm.expect_revert("only a SUBMITTED submission lapses"):
        board.lapse(submission_id)
    got = board.get_submission(submission_id)
    assert (got["status"], got["rounds"]) == ("EVALUATED", 1)


def test_a_sponsors_contest_rounds_are_the_sponsors(board, direct_vm, direct_alice,
                                                    direct_bob):
    submission_id, _first = s.evaluated(board, direct_vm, direct_alice, direct_bob)
    s.unusable(direct_vm)
    direct_vm.sender = direct_alice
    board.contest(submission_id)
    board.contest(submission_id)
    with direct_vm.expect_revert("this party has used its 2 contest rounds"):
        board.contest(submission_id)
    assert board.get_actions(submission_id, s.NOW)["may_contest"] == \
        {"developer": False, "sponsor": False}
    # the developer's own rounds are untouched: were the verdict a rejection it
    # could still contest
    s.panel(direct_vm, s.answers())
    direct_vm.warp(LATER)
    assert board.finalize(submission_id) == "PAID"


def test_checks_on_two_fields_do_not_contradict_each_other(mod, board):
    fields = s.fields() + [{"id": "sample", "label": "Sample answer", "type": "DOCUMENT",
                            "required": True}]
    requirements = [
        {"id": "c0", "kind": "CHECK", "text": "The sample is an object.",
         "check": {"type": "VALID_JSON", "field": "sample", "value": "object"}},
        {"id": "c1", "kind": "CHECK", "text": "The documentation is a list.",
         "check": {"type": "VALID_JSON", "field": "docs", "value": "array"}}]
    assert mod._parse_bounty(s.bounty_json(fields=fields, requirements=requirements))[0] == ""
    same_field = json.loads(json.dumps(requirements))
    same_field[1]["check"]["field"] = "sample"
    assert mod._parse_bounty(s.bounty_json(fields=fields, requirements=same_field))[0] == \
        "c1 and c0 cannot both be met: a file is one shape of JSON"


def test_a_header_that_arrives_as_bytes_is_read(mod, board):
    assert mod._header({"Content-Type": b"text/html; charset=utf-8"}, "content-type") == \
        "text/html; charset=utf-8"
    assert mod._header({"content-type": "application/json"}, "content-type") == \
        "application/json"
    assert mod._header({"Content-Type": bytearray(b"image/png")}, "content-type") == \
        "image/png"
    assert mod._header({}, "content-type") == "" and mod._header(None, "content-type") == ""


def test_a_listing_returns_at_most_a_page(board, mod, direct_vm, direct_alice):
    direct_vm.sender = direct_alice
    for _ in range(mod.PAGE_LIMIT + 3):
        board.create_bounty(s.bounty_json())
    page = board.list_bounties(0, 10 ** 6)
    assert (page["total"], len(page["ids"])) == (mod.PAGE_LIMIT + 3, mod.PAGE_LIMIT)
    assert page["ids"][0] == "BNT-000001" and page["ids"][-1] == "BNT-000050"
    assert board.list_bounties(mod.PAGE_LIMIT, 10)["ids"] == \
        ["BNT-000051", "BNT-000052", "BNT-000053"]


def test_the_views_say_only_what_the_writes_allow(board, direct_vm, direct_alice,
                                                  direct_bob):
    submission_id = s.submitted(board, direct_vm, direct_alice, direct_bob,
                                submit_by="2026-10-05T13:30:00Z", evaluate_window=7200)
    # a submission that may still be read does not lapse
    actions = board.get_actions(submission_id, "2026-10-05T13:30:01Z")
    assert (actions["may_evaluate"], actions["may_lapse"]) == (True, False)
    direct_vm.warp("2026-10-05T13:30:01Z")
    with direct_vm.expect_revert("this submission may be read until"):
        board.lapse(submission_id)
    # and a bounty with a submission still open is not closed, deadline or not
    assert board.get_bounty_actions("BNT-000001", "2026-10-05T13:30:01Z")["may_close"] is False
    with direct_vm.expect_revert("a submission is still open"):
        board.close("BNT-000001")
    direct_vm.warp("2026-10-05T14:00:01Z")
    actions = board.get_actions(submission_id, "2026-10-05T14:00:01Z")
    assert (actions["may_evaluate"], actions["may_lapse"]) == (False, True)
    board.lapse(submission_id)
    assert board.get_bounty_actions("BNT-000001", "2026-10-05T14:00:01Z")["may_close"] is True
    assert board.close("BNT-000001") == "RETURNED"

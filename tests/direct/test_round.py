"""One evaluation round: what makes it decide nothing, what the endpoint checks
mean, what the panel may and may not do, and what a validator refuses."""

import json

from tests.direct import support as s

AFTER_WINDOW = "2026-10-05T13:00:01Z"


def _state(board, submission_id):
    got = board.get_submission(submission_id)
    return (got["status"], got["verdict"], got["open_round"])


# -- a round that decides nothing -----------------------------------------------------

def test_a_document_that_cannot_be_fetched_decides_nothing(board, direct_vm, direct_alice,
                                                           direct_bob, direct_charlie):
    submission_id = s.submitted(board, direct_vm, direct_alice, direct_bob,
                                pages={s.DOCS_URL: s.DOWN})
    direct_vm.sender = direct_charlie                    # anyone asks for the first round
    record = s.record_of(board, board.evaluate(submission_id))
    assert (record["applied"], record["outcome"], record["reason_code"]) == \
        (False, "DELIVERABLE_UNAVAILABLE", "EVIDENCE_UNREADABLE")
    assert record["states"] == {} and record["findings"] == []
    # nothing is called once a document is unreadable
    assert [p["result"] for p in record["probes"]] == ["NOT_ASKED"] * 3
    assert _state(board, submission_id) == ("SUBMITTED", "PENDING", "EVIDENCE_UNREADABLE")
    got = board.get_submission(submission_id)
    assert got["read_by"] == "2026-10-05T13:00:00Z"      # the read-by time does not move
    actions = board.get_actions(submission_id, s.NOW)
    assert (actions["may_evaluate"], actions["evaluate_by"]) == (True, "the developer")
    # the retries are the developer's: nobody else spends them while its host is down
    for other in (direct_charlie, direct_alice):
        direct_vm.sender = other
        with direct_vm.expect_revert("only the developer asks again"):
            board.evaluate(submission_id)
    # the host comes back and the developer's round reads it
    direct_vm.clear_mocks()
    s.serve_all(direct_vm)
    s.panel(direct_vm, s.answers())
    direct_vm.sender = direct_bob
    record = s.record_of(board, board.evaluate(submission_id))
    assert (record["applied"], record["outcome"], record["round"]) == (True, "ACCEPTED", 2)
    assert _state(board, submission_id) == ("EVALUATED", "ACCEPTED", "")


def test_bytes_that_are_not_the_pinned_bytes_are_not_the_document(board, direct_vm,
                                                                  direct_alice, direct_bob):
    submission_id = s.submitted(board, direct_vm, direct_alice, direct_bob,
                                payload=s.submission(docs_body=s.DOCS + " "))
    direct_vm.sender = direct_bob
    record = s.record_of(board, board.evaluate(submission_id))
    assert record["reason_code"] == "EVIDENCE_UNREADABLE"
    assert record["sources"][0]["status"] == "DIGEST_MISMATCH"


def test_an_endpoint_that_does_not_answer_decides_nothing(board, direct_vm, direct_alice,
                                                          direct_bob, direct_charlie):
    submission_id = s.submitted(board, direct_vm, direct_alice, direct_bob,
                                pages={s.API + s.STATUS_PATH: s.DOWN})
    s.panel(direct_vm, s.answers())
    direct_vm.sender = direct_charlie
    record = s.record_of(board, board.evaluate(submission_id))
    assert (record["applied"], record["outcome"], record["reason_code"]) == \
        (False, "DELIVERABLE_UNAVAILABLE", "ENDPOINT_UNREACHABLE")
    # a service that is down has not been shown to lack a route
    assert [p["result"] for p in record["probes"]] == ["UNREACHABLE", "UNREACHABLE",
                                                       "ANSWERED"]
    assert _state(board, submission_id) == ("SUBMITTED", "PENDING", "ENDPOINT_UNREACHABLE")
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("only the developer asks again"):
        board.evaluate(submission_id)


def test_an_endpoint_is_called_once_for_each_url(board, direct_vm, direct_alice, direct_bob,
                                                 mod):
    submission_id = s.submitted(board, direct_vm, direct_alice, direct_bob)
    s.panel(direct_vm, s.answers())
    calls = []
    original = mod._call

    def counting(url):
        calls.append(url)
        return original(url)
    mod._call = counting
    try:
        direct_vm.sender = direct_bob
        board.evaluate(submission_id)
    finally:
        mod._call = original
    # two requirements read /v1/status: it is called once
    assert sorted(calls) == [s.API + s.MISSING_PATH, s.API + s.STATUS_PATH]


def test_evaluation_rounds_are_capped_and_the_read_by_time_never_moves(
        board, direct_vm, direct_alice, direct_bob):
    submission_id = s.submitted(board, direct_vm, direct_alice, direct_bob,
                                pages={s.DOCS_URL: s.DOWN})
    direct_vm.sender = direct_bob
    for minute in range(4):
        direct_vm.warp("2026-10-05T12:%02d:00Z" % (10 * minute))
        board.evaluate(submission_id)
    got = board.get_submission(submission_id)
    assert (got["rounds"], got["read_by"]) == (4, "2026-10-05T13:00:00Z")
    assert board.get_actions(submission_id, "2026-10-05T12:40:00Z")["may_evaluate"] is False
    with direct_vm.expect_revert("has used its 4 evaluation rounds"):
        board.evaluate(submission_id)
    direct_vm.warp(AFTER_WINDOW)
    assert board.lapse(submission_id) == "NOT_READ"
    got = board.get_submission(submission_id)
    assert got["result_reason"] == "DELIVERABLE_NOT_THERE_WHEN_ASKED"
    assert got["bond_fate"] == "FORFEITED_TO_SPONSOR"


def test_a_mirror_keeps_a_document_readable(board, direct_vm, direct_alice, direct_bob,
                                            direct_charlie):
    submission_id = s.submitted(board, direct_vm, direct_alice, direct_bob,
                                pages={s.DOCS_URL: s.DOWN})
    direct_vm.sender = direct_bob
    board.evaluate(submission_id)
    direct_vm.sender = direct_charlie
    with direct_vm.expect_revert("only the developer adds an address"):
        board.add_mirror(submission_id, "docs", s.DOCS_MIRROR)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("host is outside the bounty's document hosts"):
        board.add_mirror(submission_id, "docs", "https://elsewhere.example.org/docs.html")
    with direct_vm.expect_revert("field is not a document of this submission"):
        board.add_mirror(submission_id, "api", s.DOCS_MIRROR)
    with direct_vm.expect_revert("already lists that URL"):
        board.add_mirror(submission_id, "docs", s.DOCS_URL)
    commitment = board.get_submission(submission_id)["commitment"]
    assert board.add_mirror(submission_id, "docs", s.DOCS_MIRROR) == s.DOCS_MIRROR
    assert board.get_submission(submission_id)["commitment"] == commitment
    s.serve(direct_vm, s.DOCS_MIRROR, s.DOCS)
    s.panel(direct_vm, s.answers())
    record = s.record_of(board, board.evaluate(submission_id))
    assert record["outcome"] == "ACCEPTED"
    assert record["sources"][0]["served_by"] == s.DOCS_MIRROR


def test_an_unusable_answer_decides_nothing_and_either_party_may_ask_again(
        board, direct_vm, direct_alice, direct_bob, direct_charlie):
    submission_id = s.submitted(board, direct_vm, direct_alice, direct_bob)
    s.unusable(direct_vm)
    direct_vm.sender = direct_charlie
    record = s.record_of(board, board.evaluate(submission_id))
    assert (record["applied"], record["outcome"], record["panel_state"]) == \
        (False, "PANEL_UNUSABLE", "INVALID")
    assert _state(board, submission_id) == ("SUBMITTED", "PENDING", "PANEL_UNUSABLE")
    assert board.get_actions(submission_id, s.NOW)["evaluate_by"] == \
        "the developer or the sponsor"
    with direct_vm.expect_revert("only the developer or the sponsor asks again"):
        board.evaluate(submission_id)
    s.panel(direct_vm, s.answers())
    direct_vm.sender = direct_alice
    assert s.record_of(board, board.evaluate(submission_id))["outcome"] == "ACCEPTED"


def test_half_an_answer_is_no_answer(board, direct_vm, direct_alice, direct_bob):
    judged_twice = s.requirements() + [
        {"id": "r6", "kind": "JUDGED", "text": "The documentation says how often the "
                                               "timetable is refreshed."}]
    submission_id = s.submitted(board, direct_vm, direct_alice, direct_bob,
                                requirements=judged_twice)
    s.panel(direct_vm, s.answers())                      # r6 is missing
    direct_vm.sender = direct_bob
    record = s.record_of(board, board.evaluate(submission_id))
    assert record["outcome"] == "PANEL_UNUSABLE"
    both = s.answers()
    both["r6"] = s.said("MET", [("docs", s.ROUTES_LINE)])
    s.panel(direct_vm, both)
    record = s.record_of(board, board.evaluate(submission_id))
    assert record["outcome"] == "ACCEPTED" and record["states"]["r6"] == "MET"


# -- the endpoint checks --------------------------------------------------------------

def test_each_endpoint_check_is_decided_from_what_the_endpoint_answered(mod, board):
    def state(kind, value, status=200, text="{}"):
        answer = None if status is None else {"status": status, "text": text}
        return mod._probe_state({"type": kind, "field": "api", "path": "/x", "value": value},
                                answer)

    assert state("RESPONDS", 200) == "MET"
    assert state("RESPONDS", 404, status=404) == "MET"
    assert state("RESPONDS", 200, status=204) == "NOT_MET"        # the exact status
    assert state("RESPONDS", 200, status=404) == "NOT_MET"
    assert state("RESPONDS", 200, status=None) == "NOT_MET"       # no answer for it
    body = json.dumps({"service": "ok", "routes": 3})
    assert state("RESPONSE_HAS", '"service": "ok"', text=body) == "MET"
    assert state("RESPONSE_HAS", '"service": "OK"', text=body) == "NOT_MET"   # exact
    assert state("RESPONSE_HAS", "ok", status=404, text=body) == "NOT_MET"    # only on a 200
    assert state("RESPONSE_HAS", "ok", text=None) == "NOT_MET"    # too large, or not text
    assert state("RESPONSE_JSON", "object", text=body) == "MET"
    assert state("RESPONSE_JSON", "array", text=body) == "NOT_MET"
    assert state("RESPONSE_JSON", "array", text="[1, 2]") == "MET"
    assert state("RESPONSE_JSON", "any", text="3") == "MET"
    assert state("RESPONSE_JSON", "any", text="NaN") == "NOT_MET"
    assert state("RESPONSE_JSON", "any", text="<html>") == "NOT_MET"
    assert state("RESPONSE_JSON", "object", status=201, text=body) == "NOT_MET"
    assert state("RESPONSE_JSON_KEY", "routes", text=body) == "MET"
    assert state("RESPONSE_JSON_KEY", "Routes", text=body) == "NOT_MET"
    assert state("RESPONSE_JSON_KEY", "routes", text='["routes"]') == "NOT_MET"
    assert state("RESPONSE_JSON_KEY", "routes", text=chr(0xFEFF) + body) == "MET"
    assert state("RESPONSE_JSON_KEY", "routes", text=None) == "NOT_MET"


def test_what_an_endpoint_answers_is_read_strictly(board, mod, direct_vm):
    url = s.API + s.STATUS_PATH
    for body, status, expected in (
            (s.STATUS_BODY, 200, ("ANSWERED", 200, s.STATUS_BODY)),
            ("gone", 404, ("ANSWERED", 404, "gone")),
            ("", 204, ("ANSWERED", 204, "")),
            ("teapot", 418, ("ANSWERED", 418, "teapot")),
            ("moved", 301, ("ANSWERED", 301, "moved")),
            ("slow down", 429, ("UNREACHABLE", 429, None)),
            ("too slow", 408, ("UNREACHABLE", 408, None)),
            ("too early", 425, ("UNREACHABLE", 425, None)),
            ("oops", 500, ("UNREACHABLE", 500, None)),
            ("busy", 503, ("UNREACHABLE", 503, None)),
            ("odd", 99, ("UNREACHABLE", 0, None)),
            ("odd", 1000, ("UNREACHABLE", 0, None)),
            ("x" * (mod.PROBE_BYTES_CAP + 1), 200, ("ANSWERED", 200, None)),
            ("x" * mod.PROBE_BYTES_CAP, 200, ("ANSWERED", 200, "x" * mod.PROBE_BYTES_CAP)),
            (b"\xff\xfe\x00", 200, ("ANSWERED", 200, None))):
        direct_vm.clear_mocks()
        s.serve(direct_vm, url, body, status)
        answer = mod._call(url)
        assert (answer["result"], answer["status"], answer["text"]) == expected, status
    direct_vm.clear_mocks()                              # nothing is served: no answer at all
    answer = mod._call(url)
    assert (answer["result"], answer["text"]) == ("UNREACHABLE", None)


def test_a_service_that_lacks_a_route_is_rejected_not_unreachable(board, direct_vm,
                                                                  direct_alice, direct_bob):
    submission_id, evaluation_id = s.evaluated(
        board, direct_vm, direct_alice, direct_bob,
        pages={s.API + s.STATUS_PATH: s.GONE,
               s.API + s.MISSING_PATH: {"body": "everything is fine", "status": 200,
                                        "content_type": "text/plain"}})
    record = s.record_of(board, evaluation_id)
    assert record["applied"] is True and record["outcome"] == "REJECTED"
    assert record["states"] == {"r1": "NOT_MET", "r2": "NOT_MET", "r3": "NOT_MET",
                                "r4": "MET", "r5": "MET"}
    by = {f["id"]: f["by"] for f in record["findings"]}
    assert by == {"r1": "CODE", "r2": "CODE", "r3": "CODE", "r4": "CODE", "r5": "PANEL"}


def test_a_bounty_of_checks_alone_convenes_no_panel(board, direct_vm, direct_alice,
                                                    direct_bob):
    checks_only = [r for r in s.requirements() if r["kind"] == "CHECK"]
    submission_id = s.submitted(board, direct_vm, direct_alice, direct_bob,
                                requirements=checks_only)
    direct_vm._llm_mocks.clear()                         # a model call would fail
    direct_vm.sender = direct_bob
    record = s.record_of(board, board.evaluate(submission_id))
    assert (record["outcome"], record["reason_code"], record["panel_state"]) == \
        ("ACCEPTED", "CHECKS_ONLY", "NOT_NEEDED")


# -- the developer cannot talk to the panel --------------------------------------------

def test_a_document_that_addresses_the_evaluator_earns_no_reading(board, direct_vm,
                                                                 direct_alice, direct_bob):
    planted = s.page("Quayside timetable API",
                     [s.FILLER, s.AUTH_LINE, s.REVOKE_LINE, s.INJECTION])
    submission_id, evaluation_id = s.evaluated(
        board, direct_vm, direct_alice, direct_bob, pages={s.DOCS_URL: planted})
    record = s.record_of(board, evaluation_id)
    assert (record["outcome"], record["reason_code"], record["panel_state"]) == \
        ("REJECTED", "DOCUMENT_ADDRESSES_EVALUATOR", "SKIPPED")
    assert record["markers"] == ["docs:BODY"]
    assert s.finding_in(record, "r5") == {"id": "r5", "by": "CODE", "state": "NOT_MET",
                                          "quotes": [], "note": ""}
    # the checks are still decided: what the service does is not what the page says
    assert record["states"]["r1"] == "MET" and record["states"]["r4"] == "MET"


def test_the_panel_is_given_the_requirements_and_the_documents_and_nothing_else(
        board, mod, direct_vm, direct_alice, direct_bob):
    submission_id, _evaluation_id = s.evaluated(board, direct_vm, direct_alice, direct_bob)
    ctx = board._ctx(board.submissions.get(submission_id), "EVALUATE", s.NOW)
    blob = mod._panel_blob(ctx, [{"evidence_id": "docs", "status": "RETRIEVED"}],
                           {"docs": "shown text"}, {"docs": {"text": "shown text"}})
    assert sorted(blob.keys()) == ["documents", "other_requirements", "requirements",
                                   "summary", "title"]
    assert [r["id"] for r in blob["requirements"]] == ["r5"]
    assert [r["id"] for r in blob["other_requirements"]] == ["r1", "r2", "r3", "r4"]
    assert blob["documents"] == [{"evidence_id": "docs", "label": "Documentation",
                                  "text": "shown text", "shown_in_full": True}]
    # neither the endpoint nor the developer is put in front of the reader
    text = json.dumps(blob)
    assert s.API not in text and s.hexaddr(direct_bob) not in text


def test_a_met_needs_a_passage_and_the_passage_must_be_in_the_documents(
        board, direct_vm, direct_alice, direct_bob):
    invented = s.answers(quotes=[("docs", "Keys are rotated automatically every night.")])
    submission_id, evaluation_id = s.evaluated(board, direct_vm, direct_alice, direct_bob,
                                               said_by_panel=invented)
    record = s.record_of(board, evaluation_id)
    # a MET that shows nothing is not met: the burden is the developer's
    assert record["states"]["r5"] == "NOT_MET" and record["outcome"] == "REJECTED"
    assert s.finding_in(record, "r5")["quotes"] == []


# -- what a validator refuses ----------------------------------------------------------

def test_the_leaders_own_payload_is_ratified(board, direct_vm, direct_alice, direct_bob):
    s.evaluated(board, direct_vm, direct_alice, direct_bob)
    assert s.replay(direct_vm) is True


def test_forged_states_are_refused(board, direct_vm, direct_alice, direct_bob):
    s.evaluated(board, direct_vm, direct_alice, direct_bob,
                pages={s.DOCS_URL: s.DOCS_THIN}, said_by_panel=s.answers(r5="NOT_MET"))
    honest = s.leader_payload(direct_vm)
    assert s.replay(direct_vm, honest) is True
    # the judged requirement, flipped with a passage that is in the document
    forged = json.loads(json.dumps(honest))
    finding = s.finding_in(forged, "r5")
    finding["state"] = "MET"
    finding["quotes"] = [{"evidence_id": "docs", "text": s.ROUTES_LINE}]
    assert s.replay(direct_vm, forged) is False
    # a check, flipped
    forged = json.loads(json.dumps(honest))
    s.finding_in(forged, "r1")["state"] = "NOT_MET"
    assert s.replay(direct_vm, forged) is False
    # a judged requirement passed off as code's
    forged = json.loads(json.dumps(honest))
    s.finding_in(forged, "r5")["by"] = "CODE"
    assert s.replay(direct_vm, forged) is False


def test_a_forged_endpoint_answer_is_refused(board, direct_vm, direct_alice, direct_bob):
    s.evaluated(board, direct_vm, direct_alice, direct_bob,
                pages={s.API + s.STATUS_PATH: {"body": "{}", "status": 200}})
    honest = s.leader_payload(direct_vm)
    assert s.finding_in(honest, "r2")["state"] == "NOT_MET"
    forged = json.loads(json.dumps(honest))
    s.finding_in(forged, "r2")["state"] = "MET"          # the key is not there
    assert s.replay(direct_vm, forged) is False
    forged = json.loads(json.dumps(honest))
    forged["probes"][0]["result"] = "UNREACHABLE"        # it answered this validator
    forged["panel_reason"] = "ENDPOINT_UNREACHABLE"
    forged["panel_state"] = "SKIPPED"
    forged["findings"] = []
    assert s.replay(direct_vm, forged) is False
    # what the leader says the size was is its own account
    told = json.loads(json.dumps(honest))
    told["probes"][0]["byte_count"] = 12345
    assert s.replay(direct_vm, told) is True
    # but a record cannot give a call a status that contradicts the state it
    # gives the requirement that made the call, or a size nothing has
    for index, status in ((0, 404), (0, 206), (2, 200), (1, 404)):
        told = json.loads(json.dumps(honest))
        told["probes"][index]["http_status"] = status
        if index == 1:
            s.finding_in(told, "r2")["state"] = "MET"
        assert s.replay(direct_vm, told) is False, (index, status)
    told = json.loads(json.dumps(honest))
    told["probes"][0]["byte_count"] = 10 ** 9 + 1
    assert s.replay(direct_vm, told) is False


def test_validators_agree_that_a_round_decides_nothing_however_each_saw_it(
        board, direct_vm, direct_alice, direct_bob):
    submission_id = s.submitted(board, direct_vm, direct_alice, direct_bob,
                                pages={s.DOCS_URL: s.DOWN})
    direct_vm.sender = direct_bob
    board.evaluate(submission_id)
    honest = s.leader_payload(direct_vm)
    seen = json.loads(json.dumps(honest))
    seen["sources"][0]["status"] = "TIMEOUT"
    seen["sources"][0]["http_status"] = 0
    assert s.replay(direct_vm, seen) is True
    # but not a leader that says a readable deliverable was unreadable
    direct_vm.clear_mocks()
    s.serve_all(direct_vm)
    s.panel(direct_vm, s.answers())
    assert s.replay(direct_vm, honest) is False


def test_malformed_payloads_and_strange_types_are_refused(board, direct_vm, direct_alice,
                                                          direct_bob):
    s.evaluated(board, direct_vm, direct_alice, direct_bob)
    honest = s.leader_payload(direct_vm)
    for change in (
            lambda p: p.pop("probes"), lambda p: p.update(extra=1),
            lambda p: p.update(round=2), lambda p: p.update(mode="CONTEST"),
            lambda p: p.update(submission_id="SUB-000002"),
            lambda p: p.update(bounty_hash="0" * 64), lambda p: p.update(commitment="x"),
            lambda p: p.update(now="2026-10-05T12:00:01Z"),
            lambda p: p.update(schema=True), lambda p: p.update(markers=["docs:BODY"]),
            lambda p: p.update(findings=p["findings"][:-1]),
            lambda p: p.update(probes=p["probes"][:-1]),
            lambda p: p["probes"][0].update(url=s.API + "/v1/other"),
            lambda p: p["probes"][0].update(result="FINE"),
            lambda p: p["probes"][0].update(http_status="200"),
            lambda p: p["probes"][0].update(http_status=503),
            lambda p: p["probes"][0].update(result="NOT_ASKED"),
            lambda p: p["findings"][0].update(state="UNCLEAR"),
            lambda p: p["findings"][0].update(note="because"),
            lambda p: p["findings"][4].update(quotes=[]),
            lambda p: p["findings"][4]["quotes"][0].update(text="Every ... key"),
            lambda p: p["sources"][0].update(raw_sha256="0" * 64),
            lambda p: p["sources"][0].update(title="A title" + chr(0xD800)),
            lambda p: p.update(panel_state="SKIPPED")):
        forged = json.loads(json.dumps(honest))
        change(forged)
        assert s.replay(direct_vm, forged) is False
    for text in ("", "[]", "null", "not json", json.dumps([honest])):
        assert direct_vm.run_validator(leader_result=text) is False


def test_notes_and_the_choice_of_passage_may_differ(board, direct_vm, direct_alice,
                                                    direct_bob):
    s.evaluated(board, direct_vm, direct_alice, direct_bob)
    other = s.leader_payload(direct_vm)
    finding = s.finding_in(other, "r5")
    finding["note"] = "The page covers both points."
    finding["quotes"] = [{"evidence_id": "docs", "text": s.REVOKE_LINE}]
    assert s.replay(direct_vm, other) is True


def test_leader_failures_are_voted_on_by_class(board, direct_vm, direct_alice, direct_bob):
    s.evaluated(board, direct_vm, direct_alice, direct_bob)
    # this validator's own round succeeds: no leader failure is ratified
    for error in ("[TRANSIENT] the model call failed", "[EXPECTED] anything",
                  "[LLM_ERROR] refused"):
        assert s.replay(direct_vm, error=error) is False


def test_a_deliverable_asked_for_and_not_there_holds_no_place_in_line(
        board, direct_vm, direct_alice, direct_bob, direct_charlie):
    """Somebody submits first with a service that is not running. A rival asks
    for it to be evaluated at once; it is not there. From then on it does not
    stand in front of anyone: a later deliverable that is accepted is paid, and
    the placeholder's bond is forfeit."""
    placeholder = s.submitted(board, direct_vm, direct_alice, direct_bob,
                              pages={s.API + s.STATUS_PATH: s.DOWN},
                              evaluate_window=86400)
    second = s.submit(board, direct_vm, direct_charlie, "BNT-000001",
                      payload=s.submission(api="https://api.other.example.net/ferries"))
    s.serve(direct_vm, "https://api.other.example.net/ferries" + s.STATUS_PATH,
            s.STATUS_BODY)
    s.serve(direct_vm, "https://api.other.example.net/ferries" + s.MISSING_PATH, "x", 404)
    s.panel(direct_vm, s.answers())
    direct_vm.sender = direct_charlie
    board.evaluate(second)
    assert board.get_actions(second, "2026-10-05T13:00:01Z")["waits_for"] == placeholder
    board.evaluate(placeholder)                          # the rival calls it: not there
    assert board.get_submission(placeholder)["open_round"] == "ENDPOINT_UNREACHABLE"
    assert board.get_actions(second, "2026-10-05T13:00:01Z")["waits_for"] == ""
    direct_vm.warp("2026-10-05T13:00:01Z")
    assert board.finalize(second) == "PAID"
    got = board.get_submission(placeholder)
    assert (got["result"], got["result_reason"], got["bond_fate"]) == \
        ("NOT_READ", "DELIVERABLE_NOT_THERE_WHEN_ASKED", "FORFEITED_TO_SPONSOR")
    assert s.custody_holds(board, direct_vm)


def test_a_place_lost_is_won_back_by_a_round_that_reaches_the_deliverable(
        board, direct_vm, direct_alice, direct_bob, direct_charlie):
    first = s.submitted(board, direct_vm, direct_alice, direct_bob,
                        pages={s.DOCS_URL: s.DOWN}, evaluate_window=7200)
    second = s.submit(board, direct_vm, direct_charlie, "BNT-000001",
                      payload=s.submission(docs={"sha256": s.digest(s.DOCS),
                                                 "urls": [s.DOCS_MIRROR]}))
    s.serve(direct_vm, s.DOCS_MIRROR, s.DOCS)
    s.panel(direct_vm, s.answers())
    direct_vm.sender = direct_charlie
    board.evaluate(second)
    board.evaluate(first)                                # asked for: its document is not served
    assert board.get_actions(second, "2026-10-05T13:00:01Z")["waits_for"] == ""
    # its developer adds an address that serves the document, and a round reads it
    direct_vm.sender = direct_bob
    board.add_mirror(first, "docs", s.DOCS_MIRROR)
    direct_vm.warp("2026-10-05T12:50:00Z")
    board.evaluate(first)
    direct_vm.warp("2026-10-05T13:00:01Z")
    with direct_vm.expect_revert("first in line: " + first):
        board.finalize(second)
    direct_vm.warp("2026-10-05T13:50:01Z")
    assert board.finalize(first) == "PAID"
    assert board.get_submission(second)["result"] == "OUTRUN"


def test_a_submission_with_no_round_left_holds_no_place(board, direct_vm, direct_alice,
                                                        direct_bob, direct_charlie):
    first = s.submitted(board, direct_vm, direct_alice, direct_bob, evaluate_window=7200)
    second = s.submit(board, direct_vm, direct_charlie, "BNT-000001")
    s.unusable(direct_vm)
    direct_vm.sender = direct_bob
    for _ in range(3):
        board.evaluate(first)
    s.panel(direct_vm, s.answers())
    direct_vm.sender = direct_charlie
    board.evaluate(second)
    # an unusable answer is not the developer's doing: it still holds its place
    assert board.get_actions(second, "2026-10-05T13:00:01Z")["waits_for"] == first
    s.unusable(direct_vm)
    direct_vm.sender = direct_bob
    board.evaluate(first)                                # its fourth and last round
    assert board.get_actions(second, "2026-10-05T13:00:01Z")["waits_for"] == ""
    direct_vm.warp("2026-10-05T13:00:01Z")
    assert board.finalize(second) == "PAID"
    # it held no place, so it ends as it was going to end: never read
    got = board.get_submission(first)
    assert (got["result"], got["result_reason"], got["bond_fate"]) == \
        ("NOT_READ", "NO_USABLE_READING", "FORFEITED_TO_SPONSOR")


def test_a_deliverable_called_at_a_bad_moment_keeps_its_place_long_enough_to_retry(
        board, mod, direct_vm, direct_alice, direct_bob, direct_charlie):
    """Whoever asks for the first round picks the moment. A deliverable that was
    not there at that moment keeps its place for a grace period, so that its
    developer can ask again before anyone behind it is paid - and loses neither
    place nor bond if it does."""
    assert mod.RETRY_GRACE == 900
    first = s.submitted(board, direct_vm, direct_alice, direct_bob, evaluate_window=7200)
    second = s.submit(board, direct_vm, direct_charlie, "BNT-000001",
                      payload=s.submission(api="https://api.other.example.net/ferries"))
    s.serve(direct_vm, "https://api.other.example.net/ferries" + s.STATUS_PATH,
            s.STATUS_BODY)
    s.serve(direct_vm, "https://api.other.example.net/ferries" + s.MISSING_PATH, "x", 404)
    s.panel(direct_vm, s.answers())
    direct_vm.sender = direct_charlie
    board.evaluate(second)                               # accepted; its window ends 13:00
    # the rival waits for its own window to pass, then calls the first at a bad moment
    direct_vm.clear_mocks()
    s.serve_all(direct_vm, {s.API + s.STATUS_PATH: s.DOWN})
    direct_vm.warp("2026-10-05T13:00:01Z")
    board.evaluate(first)
    got = board.get_submission(first)
    assert (got["open_round"], got["open_round_at"]) == \
        ("ENDPOINT_UNREACHABLE", "2026-10-05T13:00:01Z")
    assert board.get_actions(second, "2026-10-05T13:00:01Z")["waits_for"] == first
    with direct_vm.expect_revert("first in line: " + first):
        board.finalize(second)
    direct_vm.warp("2026-10-05T13:15:01Z")               # the last second of the grace
    with direct_vm.expect_revert("first in line: " + first):
        board.finalize(second)
    assert board.get_actions(second, "2026-10-05T13:15:02Z")["waits_for"] == ""
    # the developer asks again in time, and is read
    direct_vm.clear_mocks()
    s.serve_all(direct_vm)
    s.panel(direct_vm, s.answers())
    direct_vm.sender = direct_bob
    board.evaluate(first)
    got = board.get_submission(first)
    assert (got["status"], got["open_round"], got["open_round_at"]) == ("EVALUATED", "", "")
    direct_vm.warp("2026-10-05T14:15:02Z")
    assert board.finalize(first) == "PAID"
    assert board.get_submission(second)["result"] == "OUTRUN"


def test_a_later_submission_called_at_a_bad_moment_keeps_its_bond_inside_the_grace(
        board, direct_vm, direct_alice, direct_bob, direct_charlie):
    first, _r = s.evaluated(board, direct_vm, direct_alice, direct_bob)
    direct_vm.warp("2026-10-05T12:55:00Z")
    later = s.submit(board, direct_vm, direct_charlie, "BNT-000001",
                     payload=s.submission(api="https://api.other.example.net/ferries"))
    direct_vm.sender = direct_alice                      # the sponsor calls it: not served
    record = s.record_of(board, board.evaluate(later))
    assert record["reason_code"] == "ENDPOINT_UNREACHABLE"
    direct_vm.warp("2026-10-05T13:00:01Z")
    assert board.finalize(first) == "PAID"
    got = board.get_submission(later)
    assert (got["result"], got["bond_fate"]) == ("OUTRUN", "RETURNED_TO_DEVELOPER")
    assert s.balance(board, direct_charlie) == s.BOND
    assert s.custody_holds(board, direct_vm)


def test_asking_again_and_still_not_being_there_buys_no_more_grace(
        board, direct_vm, direct_alice, direct_bob, direct_charlie):
    """The fifteen minutes run from the round that first found the deliverable
    missing. A second round that still cannot reach it, placed just before the
    grace ends, does not hold its place longer - nor, behind an accepted
    submission, turn a forfeit bond into a returned one."""
    first, _r = s.evaluated(board, direct_vm, direct_alice, direct_bob)
    later = s.submit(board, direct_vm, direct_charlie, "BNT-000001",
                     payload=s.submission(api="https://api.other.example.net/ferries"))
    direct_vm.sender = direct_alice
    board.evaluate(later)                                # called at 12:00: not there
    direct_vm.warp("2026-10-05T12:59:00Z")
    direct_vm.sender = direct_charlie
    board.evaluate(later)                                # asked again: still not there
    got = board.get_submission(later)
    assert (got["open_round"], got["open_round_at"]) == \
        ("ENDPOINT_UNREACHABLE", "2026-10-05T12:00:00Z")
    direct_vm.warp("2026-10-05T13:00:01Z")
    assert board.finalize(first) == "PAID"
    got = board.get_submission(later)
    assert (got["result"], got["result_reason"], got["bond_fate"]) == \
        ("NOT_READ", "DELIVERABLE_NOT_THERE_WHEN_ASKED", "FORFEITED_TO_SPONSOR")
    assert s.custody_holds(board, direct_vm)


def test_a_new_grace_starts_when_a_deliverable_goes_missing_again(
        board, direct_vm, direct_alice, direct_bob, direct_charlie):
    """Not there, then there but unread (the model said nothing usable), then not
    there again: that is a second time it was found missing, with its own
    fifteen minutes."""
    first = s.submitted(board, direct_vm, direct_alice, direct_bob,
                        pages={s.API + s.STATUS_PATH: s.DOWN}, evaluate_window=7200)
    direct_vm.sender = direct_bob
    board.evaluate(first)
    assert board.get_submission(first)["open_round_at"] == s.NOW
    direct_vm.clear_mocks()
    s.serve_all(direct_vm)
    s.unusable(direct_vm)
    direct_vm.warp("2026-10-05T12:10:00Z")
    board.evaluate(first)
    got = board.get_submission(first)
    assert (got["open_round"], got["open_round_at"]) == \
        ("PANEL_UNUSABLE", "2026-10-05T12:10:00Z")
    direct_vm.clear_mocks()
    s.serve_all(direct_vm, {s.API + s.STATUS_PATH: s.DOWN})
    direct_vm.warp("2026-10-05T12:40:00Z")
    board.evaluate(first)
    got = board.get_submission(first)
    assert (got["open_round"], got["open_round_at"]) == \
        ("ENDPOINT_UNREACHABLE", "2026-10-05T12:40:00Z")

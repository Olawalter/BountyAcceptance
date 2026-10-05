"""The checks that read a pinned document, what the panel is told about a document
it sees only in part, and answers the model may not give."""

import json

from tests.direct import support as s


def _doc(text, raw=None, partial=False) -> dict:
    raw = text if raw is None else raw
    return {"bytes": len(raw.encode("utf-8")) if raw is not None else 0, "raw": raw,
            "text": text, "partial": partial}


def _state(mod, kind, value, doc) -> str:
    return mod._check_state({"type": kind, "field": "d", "value": value},
                            {} if doc is None else {"d": doc}, {}, "c")


def test_each_document_check_is_decided_from_the_document(mod, board):
    doc = _doc("Every request carries an API key. Keys are issued from the dashboard.")
    assert _state(mod, "CONTAINS", "api key", doc) == "MET"
    assert _state(mod, "CONTAINS", "session cookie", doc) == "NOT_MET"
    assert _state(mod, "NOT_CONTAINS", "session cookie", doc) == "MET"
    assert _state(mod, "NOT_CONTAINS", "API   KEY", doc) == "NOT_MET"
    assert _state(mod, "MIN_WORDS", 12, doc) == "MET"
    assert _state(mod, "MIN_WORDS", 13, doc) == "NOT_MET"
    for raw, shape, expected in (
            ('{"a": 1}', "object", "MET"), ('{"a": 1}', "array", "NOT_MET"),
            ('{"a": 1}', "any", "MET"), ("[1, 2]", "array", "MET"),
            ("[1, 2]", "object", "NOT_MET"), ("7", "any", "MET"), ("7", "object", "NOT_MET"),
            ('"text"', "array", "NOT_MET"), ("{not json", "any", "NOT_MET"),
            ("{not json", "object", "NOT_MET"), ("", "any", "NOT_MET"),
            ("NaN", "any", "NOT_MET"), ('{"a": Infinity}', "object", "NOT_MET"),
            (chr(0xFEFF) + '{"a": 1}', "object", "MET")):
        assert _state(mod, "VALID_JSON", shape, _doc(raw)) == expected, (raw, shape)


def test_a_check_on_what_is_not_there_is_not_met(mod, board):
    for kind, value in (("CONTAINS", "api key"), ("NOT_CONTAINS", "api key"),
                        ("MIN_WORDS", 1), ("VALID_JSON", "any")):
        # the field was left out, or its bytes are not text
        assert _state(mod, kind, value, None) == "NOT_MET", kind
        assert _state(mod, kind, value, {"bytes": 9, "raw": None, "text": "",
                                         "partial": False}) == "NOT_MET", kind


def test_absence_is_not_shown_by_the_first_part_of_a_document(mod, board):
    whole = _doc("No session cookie is used anywhere.")
    part = _doc("No session cookie is used anywhere.", partial=True)
    assert _state(mod, "NOT_CONTAINS", "password", whole) == "MET"
    assert _state(mod, "NOT_CONTAINS", "password", part) == "NOT_MET"
    assert _state(mod, "VALID_JSON", "any", _doc("[1]", partial=True)) == "NOT_MET"
    # what a part does show, it shows
    assert _state(mod, "CONTAINS", "session cookie", part) == "MET"
    assert _state(mod, "MIN_WORDS", 6, part) == "MET"


def test_a_document_longer_than_a_node_reads_is_read_in_part(board, mod, direct_vm,
                                                             direct_alice, direct_bob):
    long_docs = s.page("Quayside timetable API",
                       [s.FILLER, s.AUTH_LINE, s.REVOKE_LINE]
                       + ["Padding sentence number %d of many." % i for i in range(9000)])
    assert len(long_docs.encode("utf-8")) > mod.BODY_BYTES_CAP
    requirements = s.requirements() + [
        {"id": "r6", "kind": "CHECK", "text": "The documentation has no placeholder text.",
         "check": {"type": "NOT_CONTAINS", "field": "docs", "value": "lorem ipsum"}}]
    submission_id, evaluation_id = s.evaluated(
        board, direct_vm, direct_alice, direct_bob, pages={s.DOCS_URL: long_docs},
        requirements=requirements)
    record = s.record_of(board, evaluation_id)
    source = s.source_in(record, "docs")
    assert (source["status"], source["truncated"]) == ("PARTIAL", True)
    assert source["byte_count"] == len(long_docs.encode("utf-8"))
    # the part that was read meets what it shows, and shows no absence
    assert record["states"]["r4"] == "MET" and record["states"]["r5"] == "MET"
    assert record["states"]["r6"] == "NOT_MET" and record["outcome"] == "REJECTED"


def test_the_panel_is_told_when_it_sees_only_the_first_part(mod, board):
    ctx = {"bounty": s.bounty(), "items": [{"evidence_id": "docs", "label": "Documentation"}]}
    docs = {"docs": {"text": "abcdefghij"}}
    whole = mod._panel_blob(ctx, [{"evidence_id": "docs", "status": "RETRIEVED"}],
                            {"docs": "abcdefghij"}, docs)
    assert whole["documents"][0]["shown_in_full"] is True
    cut_by_budget = mod._panel_blob(ctx, [{"evidence_id": "docs", "status": "RETRIEVED"}],
                                    {"docs": "abcde"}, docs)
    assert cut_by_budget["documents"][0]["shown_in_full"] is False
    cut_by_cap = mod._panel_blob(ctx, [{"evidence_id": "docs", "status": "PARTIAL"}],
                                 {"docs": "abcdefghij"}, docs)
    assert cut_by_cap["documents"][0]["shown_in_full"] is False


def test_a_state_nobody_defined_is_no_answer(board, direct_vm, direct_alice, direct_bob):
    for answer in ({"r5": {"state": "PARTLY_MET"}}, {"r5": {"state": "UNCLEAR"}},
                   {"r5": {"state": ""}}, {"r5": {"state": 1}}, {"r5": {"verdict": "MET"}},
                   {"r5": ["MET"]}, {"r5": None}, {}):
        direct_vm.clear_mocks()
        submission_id = s.submitted(board, direct_vm, direct_alice, direct_bob)
        s.panel(direct_vm, answer)
        direct_vm.sender = direct_bob
        record = s.record_of(board, board.evaluate(submission_id))
        assert record["outcome"] == "PANEL_UNUSABLE", answer
        direct_vm.warp("2026-10-05T13:00:01Z")
        board.lapse(submission_id)
        direct_vm.warp(s.NOW)


def test_loose_but_meaningful_answers_are_read(board, direct_vm, direct_alice, direct_bob):
    submission_id = s.submitted(board, direct_vm, direct_alice, direct_bob)
    direct_vm._llm_mocks.clear()
    # no wrapper object, a lowercase state, a quote given as a bare string
    direct_vm.mock_llm("Bounty acceptance panel", json.dumps(
        {"R5": {"state": " met ", "quotes": s.AUTH_LINE, "note": "Both points are covered."}}))
    direct_vm.sender = direct_bob
    record = s.record_of(board, board.evaluate(submission_id))
    finding = s.finding_in(record, "r5")
    assert (finding["state"], finding["note"]) == ("MET", "Both points are covered.")
    assert finding["quotes"] == [{"evidence_id": "docs", "text": s.AUTH_LINE}]


def test_text_a_sponsor_writes_is_printable(mod, board):
    for bad in ("two" + chr(0) + "words", "a" + chr(7) + "b", "a" + chr(0x2028) + "b",
                "a" + chr(0x2029) + "b", "a" + chr(0x85) + "b", "a" + chr(0x1B) + "[0m"):
        assert mod._text_error(bad, 600, "summary", True) != "", ascii(bad)
        assert mod._parse_bounty(s.bounty_json(summary=bad))[0] != "", ascii(bad)
    assert mod._text_error("one line" + chr(10) + "another", 600, "summary", True) == ""
    assert mod._text_error("one line" + chr(10) + "another", 600, "title", False) != ""
    assert mod._text_error("a" + chr(9) + "b", 600, "summary", True) != ""


def test_a_document_that_is_not_text_is_part_of_the_deliverable_with_no_text(
        board, direct_vm, direct_alice, direct_bob):
    picture = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0xFF, 0xFE, 0x00, 0x01])
    submission_id = s.submitted(
        board, direct_vm, direct_alice, direct_bob,
        pages={s.DOCS_URL: {"body": picture, "content_type": "image/png"}},
        payload=s.submission(docs={"sha256": s.digest(picture), "urls": [s.DOCS_URL]}))
    direct_vm._llm_mocks.clear()                         # no panel is convened
    direct_vm.sender = direct_bob
    record = s.record_of(board, board.evaluate(submission_id))
    assert s.source_in(record, "docs")["status"] == "NOT_TEXT"
    assert (record["applied"], record["outcome"], record["reason_code"]) == \
        (True, "REJECTED", "NOTHING_TO_READ")
    assert record["states"] == {"r1": "MET", "r2": "MET", "r3": "MET", "r4": "NOT_MET",
                                "r5": "NOT_MET"}

"""The MCP surface.

A calling model paraphrases whatever it is handed, so this surface has one
failure mode that matters more than any protocol bug: a label that reads as an
accusation. "NOT_FOUND" becomes "not found" becomes "this case does not exist"
in three hops, and the tool's entire discipline is lost in transit. Most of
these tests guard the vocabulary rather than the plumbing.
"""

import json

import pytest

from verascite.mcp_server import (
    CONTRACT,
    PROTOCOL_VERSION,
    TOOLS,
    WIRE,
    extract_only,
    handle,
)
from verascite.verdicts import Overall

BRIEF = (
    'A pleading must contain more than "labels and conclusions." Bell Atlantic '
    "Corp. v. Twombly, 550 U.S. 544, 555 (2007). See also Smith v. Fictional "
    "Reporter Co., 88 Jurisprudentia 100 (9th Cir. 2018), and Tinch v. Video "
    "Indus. Servs., Inc., 2019 WL 1396975."
)


def call(name, **args):
    response = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": name, "arguments": args}})
    return json.loads(response["result"]["content"][0]["text"]), response["result"]


# --- protocol -----------------------------------------------------------------

def test_initialize_advertises_the_server():
    r = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert r["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert r["result"]["serverInfo"]["name"] == "verascite"


def test_initialize_ships_the_reporting_contract():
    """The constraint must travel with the connection, not sit in a readme."""
    r = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert "does NOT mean the citation is" in r["result"]["instructions"]


def test_a_notification_gets_no_response():
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_are_listed_with_schemas():
    r = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in r["result"]["tools"]}
    assert names == {"verify_citations", "extract_citations"}
    for tool in r["result"]["tools"]:
        assert tool["inputSchema"]["type"] == "object"


def test_unknown_method_is_an_error_not_a_crash():
    r = handle({"jsonrpc": "2.0", "id": 1, "method": "nope"})
    assert r["error"]["code"] == -32601


def test_unknown_tool_is_an_error():
    r = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "wishful", "arguments": {}}})
    assert r["error"]["code"] == -32601


# --- the vocabulary, which is the point ---------------------------------------

def test_no_wire_label_can_be_read_as_fabricated():
    for label in WIRE.values():
        low = label.lower()
        for word in ("fake", "fabricat", "hallucinat", "invent", "not_found", "nonexistent"):
            assert word not in low, f"wire label {label!r} invites the wrong reading"


def test_absence_is_called_unverified_on_the_wire():
    assert WIRE[Overall.UNVERIFIED] == "unverified"
    assert WIRE[Overall.FLAGGED] == "contradicted"


def test_every_result_carries_the_reporting_contract():
    payload, _ = call("verify_citations", text=BRIEF, offline=True)
    assert "does NOT mean the citation is" in payload["how_to_report_this"]
    assert "not legal advice" in payload["how_to_report_this"]


def test_an_unverified_citation_carries_its_own_disclaimer():
    payload, _ = call("verify_citations", text=BRIEF, offline=True)
    unverified = [c for c in payload["citations"] if c["result"] == "unverified"]
    assert unverified
    for citation in unverified:
        assert "not a finding that the" in citation["note"]


def test_the_tool_description_warns_the_caller():
    tool = next(t for t in TOOLS if t["name"] == "verify_citations")
    assert "does NOT mean the citation is fabricated" in tool["description"]


# --- results ------------------------------------------------------------------

def test_a_fabricated_reporter_is_contradicted_with_evidence():
    payload, _ = call("verify_citations", text=BRIEF, offline=True)
    bad = [c for c in payload["citations"] if "Jurisprudentia" in c["citation"]]
    assert bad and bad[0]["result"] == "contradicted"
    assert bad[0]["evidence"][0]["finding"]


def test_a_vendor_only_identifier_is_never_contradicted():
    """The governing rule, on the surface most likely to be paraphrased."""
    payload, _ = call("verify_citations", text=BRIEF, offline=True)
    wl = [c for c in payload["citations"] if "WL" in c["citation"]]
    assert wl and wl[0]["result"] != "contradicted"


def test_results_are_ordered_most_severe_first():
    payload, _ = call("verify_citations", text=BRIEF, offline=True)
    assert payload["citations"][0]["result"] == "contradicted"


def test_coverage_is_reported_alongside_the_counts():
    payload, _ = call("verify_citations", text=BRIEF, offline=True)
    assert payload["coverage"]
    assert payload["summary"]["citations"] == 3


def test_extract_makes_no_claim_about_any_citation():
    payload, _ = call("extract_citations", text=BRIEF)
    assert payload["count"] == 3
    assert "result" not in json.dumps(payload)


# --- failure is not a verdict -------------------------------------------------

def test_a_bad_request_returns_an_error_not_a_finding():
    payload, result = call("verify_citations")   # neither text nor path
    assert result.get("isError")
    assert "not a finding about any citation" in payload["note"]


def test_a_missing_file_does_not_become_a_verdict():
    payload, result = call("verify_citations", path="/nonexistent/brief.pdf")
    assert result.get("isError")
    assert "error" in payload

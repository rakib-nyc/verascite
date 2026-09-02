"""No artifact may contain the API token.

The premise of this tool is that a lawyer can run it over a confidential
document and hand the output to a partner. An artifact that carries the
operator's API credential breaks that premise, and `ledger.json` and
`report.md` are both explicitly meant to be forwarded.
"""

from __future__ import annotations

import json
import re

import pytest

from verascite.clients.courtlistener import CourtListenerClient, LookupResult
from verascite.extract import extract
from verascite.models import Document
from verascite.report import render_report
from verascite.resolve import resolve
from verascite.verify_existence import _apply_lookup, verify_existence
from verascite.verify_metadata import verify_metadata

SENTINEL = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c"
BRIEF = 'The Court held that "a formulaic recitation" suffices. Harlow v. Fitzgerald, 457 U.S. 800, 818 (1982).'


def _audit_with_token(tmp_path, token: str):
    client = CourtListenerClient(token=token, cache_dir=tmp_path / "cache")
    document = Document(
        text=BRIEF, source_path="confidential-brief.docx", source_format="docx",
        page_map=[(0, len(BRIEF), 1)],
    )
    citations, notes = extract(document)
    ledger = resolve(document, citations, notes)
    verify_existence(ledger, client=None, offline=True)
    for entry in ledger:
        entry.checks.pop("existence", None)
        _apply_lookup(
            entry,
            LookupResult(
                citation=entry.citation.normalized,
                status=200,
                clusters=[{"id": 1, "case_name": "Harlow v. Fitzgerald",
                           "date_filed": "1982-06-24", "absolute_url": "/opinion/1/h/",
                           "precedential_status": "Published"}],
            ),
        )
    verify_metadata(ledger)
    client.cache.put(LookupResult(citation="457 U.S. 800", status=200, clusters=[]))

    ledger_path = tmp_path / "ledger.json"
    ledger.checkpoint(ledger_path)
    report_path = tmp_path / "report.md"
    report_path.write_text(render_report(ledger), encoding="utf-8")
    return tmp_path


def test_token_reaches_no_artifact(tmp_path):
    out = _audit_with_token(tmp_path, SENTINEL)
    written = [p for p in out.rglob("*") if p.is_file()]
    assert written, "the audit produced no files to check"
    for path in written:
        blob = path.read_text(encoding="utf-8", errors="replace")
        assert SENTINEL not in blob, f"token leaked into {path.name}"
        assert SENTINEL not in path.name, f"token leaked into the filename {path.name}"


def test_no_artifact_contains_anything_shaped_like_a_token(tmp_path):
    """Catches a credential this test did not know to look for."""
    out = _audit_with_token(tmp_path, SENTINEL)
    pattern = re.compile(r"\b[0-9a-f]{40}\b")
    for path in out.rglob("*"):
        if path.is_file():
            found = pattern.findall(path.read_text(encoding="utf-8", errors="replace"))
            assert not found, f"{path.name} contains token-shaped strings: {found[:2]}"


def test_ledger_records_source_names_not_credentials(tmp_path):
    out = _audit_with_token(tmp_path, SENTINEL)
    data = json.loads((out / "ledger.json").read_text())
    blob = json.dumps(data).lower()
    for forbidden in ("authorization", "token ", "api_key", "apikey", "bearer"):
        assert forbidden not in blob, f"{forbidden!r} appears in the ledger"


def test_client_never_writes_the_token_into_its_cache(tmp_path):
    client = CourtListenerClient(token=SENTINEL, cache_dir=tmp_path)
    client.cache.put(
        LookupResult(citation="457 U.S. 800", status=200, clusters=[{"id": 1}])
    )
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert SENTINEL not in path.read_text(encoding="utf-8", errors="replace")


def test_user_agent_carries_no_credential():
    client = CourtListenerClient(token=SENTINEL)
    assert SENTINEL not in client.user_agent


def test_environment_token_is_not_visible_to_the_suite():
    """conftest strips it, so a developer's live token cannot mask an assertion."""
    import os

    assert os.environ.get("COURTLISTENER_API_TOKEN") is None


def test_network_is_blocked_in_the_unit_suite():
    """A test that reaches for the network fails by name, not silently."""
    import socket

    from conftest import NetworkBlocked

    with pytest.raises(NetworkBlocked, match="must not reach the network"):
        socket.create_connection(("courtlistener.com", 443), timeout=1)

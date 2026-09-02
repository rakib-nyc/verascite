"""Verdicts locked against a fixed corpus.

If a threshold in verascite/config.py moves, this fails. That is the point:
M5 will tune numerics for proposition and treatment checks, and it must not be
possible to shift an M1 or M2 verdict as a side effect.
"""

from __future__ import annotations

import json

import pytest

from verascite import config
from verascite.clients.courtlistener import LookupResult
from verascite.extract import extract
from verascite.models import Document
from verascite.resolve import resolve
from verascite.verify_existence import _apply_lookup, verify_existence
from verascite.verify_metadata import verify_metadata
from verascite.verify_quote import verify_quote

from fixtures.corpus import BRIEF, EXPECTED, LOOKUPS, twombly_opinions


def build_ledger():
    document = Document(
        text=BRIEF, source_path="corpus", source_format="md",
        page_map=[(0, len(BRIEF), 1)],
    )
    citations, notes = extract(document)
    ledger = resolve(document, citations, notes)

    verify_existence(ledger, client=None, offline=True)
    for entry in ledger:
        canned = LOOKUPS.get(entry.citation.normalized or "")
        if canned and not entry.antecedent_id:
            entry.checks.pop("existence", None)
            _apply_lookup(
                entry,
                LookupResult(
                    citation=entry.citation.normalized,
                    status=canned["status"],
                    clusters=canned["clusters"],
                ),
            )
    # Short forms inherit from their antecedent, as the live path does.
    for entry in ledger:
        if entry.antecedent_id:
            parent = ledger.get(entry.antecedent_id)
            if parent and parent.resolved_to:
                entry.resolved_to = parent.resolved_to
                entry.checks["existence"] = parent.checks["existence"]

    verify_metadata(ledger)
    for entry in ledger:
        if entry.citation.quoted_language:
            cluster = entry.resolved_to.cluster_id if entry.resolved_to else None
            verify_quote(entry, twombly_opinions() if cluster == 145730 else [])
    return ledger


@pytest.fixture(scope="module")
def ledger():
    return build_ledger()


def test_threshold_version_is_pinned():
    """Bumping this is a deliberate act; it should require touching this test."""
    assert config.THRESHOLDS_VERSION == "2"
    assert config.QUOTE_MATERIAL_ALTERATION_FLOOR == 0.75
    assert config.QUOTE_NEAR_MISS_FLOOR == 0.60
    assert config.NAME_PASS_THRESHOLD == 0.60


def test_corpus_extracts_the_expected_citations(ledger):
    assert len(ledger) == len(EXPECTED), [e.citation.raw_text for e in ledger]


@pytest.mark.parametrize("citation_id", sorted(EXPECTED))
def test_locked_overall_verdict(ledger, citation_id):
    expected_overall, _ = EXPECTED[citation_id]
    entry = ledger.get(citation_id)
    assert entry is not None, f"{citation_id} missing from the ledger"
    assert entry.overall.value == expected_overall, (
        f"{citation_id} ({entry.citation.raw_text!r}) is {entry.overall.value}, "
        f"expected {expected_overall}"
    )


@pytest.mark.parametrize("citation_id", sorted(EXPECTED))
def test_locked_dimension_verdicts(ledger, citation_id):
    _, dimensions = EXPECTED[citation_id]
    entry = ledger.get(citation_id)
    for dimension, expected in dimensions.items():
        actual = entry.checks.get(dimension)
        assert actual is not None, f"{citation_id}.{dimension} was never checked"
        assert actual.verdict.value == expected, (
            f"{citation_id}.{dimension} is {actual.verdict.value}, expected {expected}"
        )


def test_no_absence_verdict_ever_produces_flagged(ledger):
    """The P1 firewall, restated over a realistic document."""
    for entry in ledger:
        if entry.overall.value == "FLAGGED":
            assert any(c.verdict.value == "FAIL" for c in entry.checks.values()), (
                f"{entry.citation_id} is FLAGGED without any FAIL dimension"
            )


def test_quote_accounting_is_reported(ledger):
    quotes = ledger.run_meta["quotes"]
    assert quotes["found_in_document"] == 4
    assert quotes["attributed_to_a_citation"] == 4
    assert quotes["unattributed"] == 0


def _verdict_shape(ledger) -> str:
    """Verdicts and evidence, without the wall-clock fields.

    Retrieval timestamps legitimately differ between runs. What the spec
    requires to be identical is the verdicts, so that is what is compared.
    """
    return json.dumps(
        [
            {
                "citation_id": e.citation_id,
                "raw_text": e.citation.raw_text,
                "overall": e.overall.value,
                "checks": {
                    name: [c.verdict.value, c.evidence, c.reason]
                    for name, c in sorted(e.checks.items())
                },
            }
            for e in ledger
        ],
        sort_keys=True,
    )


def test_corpus_is_deterministic():
    """Spec 8.2: divergence in the deterministic layer is a bug."""
    runs = [_verdict_shape(build_ledger()) for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]

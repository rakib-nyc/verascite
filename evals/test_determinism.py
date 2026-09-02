"""Eval: the deterministic layer must not vary between runs.

Spec 8.2 calls divergence here a bug. It is a bug that occurs in practice --
a swallowed 429 changed which sub-opinions were searched from one run to the
next, and nothing in the output said so. That failure was invisible precisely
because no assertion was watching for it.

This runs the full offline pipeline over each fixture three times and requires
the ledgers to be byte-identical once wall-clock fields are removed. Run with:

    python -m pytest evals/ -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from verascite.extract import extract
from verascite.ingest import ingest
from verascite.resolve import resolve
from verascite.verify_existence import verify_existence
from verascite.verify_metadata import verify_metadata

FIXTURES = sorted((ROOT / "tests" / "fixtures").glob("*.md"))

#: Fields that legitimately differ between runs and say nothing about verdicts.
VOLATILE = {"created_at", "updated_at", "retrieved_at", "run_at", "oldest_evidence"}


def _strip_volatile(value):
    if isinstance(value, dict):
        return {k: _strip_volatile(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [_strip_volatile(v) for v in value]
    return value


def _run_offline(path: Path) -> str:
    document = ingest(path)
    citations, notes = extract(document)
    ledger = resolve(document, citations, notes)
    verify_existence(ledger, client=None, offline=True)
    verify_metadata(ledger)
    return json.dumps(_strip_volatile(ledger.to_dict()), sort_keys=True, indent=None)


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.name)
def test_deterministic_layer_is_byte_identical_across_runs(fixture):
    runs = [_run_offline(fixture) for _ in range(3)]
    if runs[0] != runs[1] or runs[1] != runs[2]:
        first = json.loads(runs[0])
        for index, other in enumerate(runs[1:], start=2):
            other = json.loads(other)
            for a, b in zip(first["entries"], other["entries"]):
                if a != b:
                    pytest.fail(
                        f"run 1 and run {index} diverged on {a['citation_id']}: "
                        f"{a['overall']} vs {b['overall']}\n{a}\n{b}"
                    )
        pytest.fail("ledgers diverged outside the entry list")


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.name)
def test_extraction_offsets_address_the_real_document(fixture):
    """Every verdict points at a location a reader can find."""
    document = ingest(fixture)
    citations, _ = extract(document)
    assert citations, f"no citations extracted from {fixture.name}"
    for citation in citations:
        span = document.text[
            citation.location.char_start : citation.location.char_end
        ]
        assert span == citation.raw_text


def test_fixtures_exist():
    assert FIXTURES, "no fixture documents found"

"""The P1 invariant: absence of evidence is never evidence of fabrication.

These are the tests the whitepaper's opening instruction makes binding --
NOT_FOUND and FABRICATED must be structurally distinct in the data model, not
merely different strings. Everything here is enumerative rather than
example-based, because "we could not conflate them by accident" is a claim
about *all* inputs.
"""

from __future__ import annotations

import itertools

import pytest

from verascite.verdicts import (
    ABSENCE_VERDICTS,
    DIMENSIONS,
    FABRICATION_SIGNALS,
    CheckResult,
    Overall,
    Verdict,
    rollup,
)


def make(verdict: Verdict) -> CheckResult:
    """Construct a CheckResult of any verdict, satisfying the interlocks."""
    if verdict is Verdict.FAIL:
        return CheckResult(verdict, evidence="contradicted by the retrieved source")
    if verdict is Verdict.NOT_FOUND:
        return CheckResult(verdict, sources_consulted=["courtlistener"])
    if verdict in (Verdict.NOT_CHECKABLE, Verdict.OUT_OF_SCOPE):
        return CheckResult(verdict, reason="stated reason")
    return CheckResult(verdict)


def test_there_is_no_fabricated_verdict():
    """The vocabulary must not contain a verdict that asserts fabrication."""
    names = {v.name for v in Verdict} | {o.name for o in Overall}
    assert not {n for n in names if "FABRICAT" in n or "FAKE" in n}


def test_verdict_sets_are_disjoint():
    assert not (FABRICATION_SIGNALS & ABSENCE_VERDICTS)


def test_absence_alone_never_flags():
    """No combination of absence verdicts may ever roll up to FLAGGED."""
    for size in (1, 2, 3):
        for combo in itertools.product(sorted(ABSENCE_VERDICTS, key=str), repeat=size):
            checks = {dim: make(v) for dim, v in zip(DIMENSIONS, combo)}
            assert rollup(checks) is not Overall.FLAGGED, combo


def test_not_found_existence_rolls_up_to_unverified_not_flagged():
    for extra in (Verdict.PASS, Verdict.NA, Verdict.NOT_CHECKABLE, Verdict.AMBIGUOUS):
        checks = {"existence": make(Verdict.NOT_FOUND), "case_name": make(extra)}
        assert rollup(checks) is Overall.UNVERIFIED


def test_only_fail_can_produce_flagged():
    """Exhaustive: FLAGGED appears iff some dimension is FAIL."""
    for combo in itertools.product(sorted(Verdict, key=str), repeat=2):
        checks = {dim: make(v) for dim, v in zip(DIMENSIONS, combo)}
        flagged = rollup(checks) is Overall.FLAGGED
        assert flagged == any(v is Verdict.FAIL for v in combo), combo


def test_fail_requires_evidence():
    with pytest.raises(ValueError, match="requires affirmative evidence"):
        CheckResult(Verdict.FAIL)
    with pytest.raises(ValueError):
        CheckResult(Verdict.FAIL, evidence="   ")
    with pytest.raises(ValueError):
        CheckResult(Verdict.FAIL, reason="looks wrong")  # reason is not evidence


def test_not_found_requires_named_sources():
    with pytest.raises(ValueError, match="sources_consulted"):
        CheckResult(Verdict.NOT_FOUND)
    with pytest.raises(ValueError):
        CheckResult(Verdict.NOT_FOUND, reason="could not find it")
    assert CheckResult(Verdict.NOT_FOUND, sources_consulted=["courtlistener"])


def test_uncheckable_and_out_of_scope_require_a_reason():
    for verdict in (Verdict.NOT_CHECKABLE, Verdict.OUT_OF_SCOPE):
        with pytest.raises(ValueError, match="requires a stated reason"):
            CheckResult(verdict)


def test_default_is_pending_not_pass():
    """P6: nothing passes by omission."""
    assert rollup({}) is Overall.PENDING
    assert rollup({"existence": CheckResult(Verdict.PENDING)}) is Overall.PENDING


def test_verified_requires_affirmative_pass_on_every_applicable_dimension():
    assert rollup({"existence": make(Verdict.PASS), "year": make(Verdict.NA)}) is Overall.VERIFIED
    assert rollup(
        {"existence": make(Verdict.PASS), "year": make(Verdict.NOT_CHECKABLE)}
    ) is Overall.REVIEW


def test_remediation_text_distinguishes_absence_from_contradiction():
    not_found = CheckResult(Verdict.NOT_FOUND, sources_consulted=["courtlistener"])
    fail = CheckResult(Verdict.FAIL, evidence="wrong court")
    assert "NOT a finding" in not_found.remediation
    assert not not_found.is_fabrication_signal
    assert fail.is_fabrication_signal

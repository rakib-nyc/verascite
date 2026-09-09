"""Quotation agreement, calibrated to the document rather than to a constant.

**The rejection this replaces.** Flagging quotations that do not match their
source was built, measured, and deliberately not shipped. The reason is in
`evals/lephantomcite/V8_PROTOCOL.md`: a *correct* quotation matched its source
at a median of 0.929 rather than 1.00, because both the brief and the archive
were scanned text. The injected defects were one- and two-word swaps, which are
smaller than the disagreement the scanning already produced. Every fixed
threshold was swept and precision peaked at 17.2%.

**What the new measurement changed.** `evals/misquote/` re-ran that sweep on
opinion text the archive itself marks as not scanned, and found something other
than the expected answer. The archive's own scanning flag is **not** the
variable: the scanned and unscanned subsets behave almost identically at every
noise level. What decides the outcome is how closely *this document* and the
archive agree, and a fixed threshold cannot know that. It is applied with the
same number to a brief whose quotations agree at 0.999 and to one whose
quotations agree at 0.95, and it is wrong for one of them.

**The idea.** A document carries the evidence needed to calibrate itself. A
brief quotes many authorities. If nine of its quotations match their sources at
0.999 and the tenth matches at 0.977, the tenth is anomalous *for this
document* -- and the comparison is between quotations that travelled through
the same author, the same word processor, and the same extraction. If all ten
match at 0.95, the document is noisy, nothing separates a misquote from the
background, and the correct output is silence.

**Measured, at `evals/misquote/RESULTS.md`.** Across simulated documents at
several disagreement levels, an outlier margin of 0.02 gave 80-91% precision at
35-56% recall, against 17.2% precision for the best fixed threshold. Where
disagreement was high the gate switched the check off, declining 28 of 29
documents rather than guessing.

**Why this only ever produces REVIEW.** Two reasons, and the second is the
binding one. The evidence is from synthetic disagreement rather than from real
scanned briefs, so the precision figure is corroborated rather than proven. And
an anomaly in agreement is not contradiction: the honest statement is "this
quotation matches its source less well than the rest of yours do, read it",
which is a prompt to look, not a finding against the document. `FAIL` remains
reserved for the quotation check proper, which requires retrieved text that
actually contradicts.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Optional

from .ledger import Ledger
from .verdicts import CheckResult, Verdict

AGREEMENT_SOURCE = "document_calibrated_quotation_agreement"

#: Fewer located quotations than this establish no distribution to calibrate
#: against, and treating two ratios as one is how a check begins inventing
#: findings on thin evidence.
MIN_QUOTES_TO_CALIBRATE = 6

#: A document whose own quotations agree with their sources less well than this
#: is too noisy to calibrate. Below it the altered and unaltered distributions
#: overlap and every finding would be a coin toss wearing evidence's clothes.
MIN_DOCUMENT_AGREEMENT = 0.99

#: How far below the document's own level a quotation must sit to be called
#: anomalous. Swept in evals/misquote/; 0.02 was the precision knee.
OUTLIER_MARGIN = 0.02


@dataclass
class AgreementReading:
    """What the calibration concluded about one document."""

    quotations: int
    baseline: Optional[float] = None
    declined: str = ""
    flagged: int = 0

    def to_dict(self) -> dict:
        out = {"quotations_located": self.quotations, "flagged": self.flagged}
        if self.baseline is not None:
            out["document_agreement"] = round(self.baseline, 4)
        if self.declined:
            out["declined"] = self.declined
        out["margin"] = OUTLIER_MARGIN
        out["note"] = (
            "an anomaly here is a prompt to read the quotation, not a finding "
            "that it is wrong. Calibrated to this document's own agreement with "
            "its sources"
        )
        return out


def _located_ratios(ledger: Ledger) -> list[tuple[object, float]]:
    """Every quotation that was actually found in a retrieved source.

    Only located quotations calibrate. A quotation that was never found tells
    us nothing about how well this document transcribes, and including it would
    drag the baseline down and suppress the whole check.
    """
    out = []
    for entry in ledger:
        check = entry.checks.get("quote")
        if check is None or not isinstance(check.detail, dict):
            continue
        located = [
            q for q in (check.detail.get("quotes") or [])
            if isinstance(q, dict) and q.get("found")
            and isinstance(q.get("similarity"), (int, float))
        ]
        if not located:
            continue
        # One entry may carry several quotations. The weakest is the one that
        # would be anomalous, so it is the one that represents the entry.
        ratio = min(float(q["similarity"]) for q in located)
        if 0.0 < ratio <= 1.0:
            out.append((entry, ratio))
    return out


def verify_agreement(ledger: Ledger) -> AgreementReading:
    """Flag quotations that agree with their source worse than the rest do."""
    pairs = _located_ratios(ledger)
    reading = AgreementReading(quotations=len(pairs))

    if len(pairs) < MIN_QUOTES_TO_CALIBRATE:
        reading.declined = (
            f"only {len(pairs)} quotation(s) were located in a retrieved source, "
            f"and at least {MIN_QUOTES_TO_CALIBRATE} are needed before this "
            "document's own level of agreement means anything"
        )
        return reading

    ratios = [ratio for _, ratio in pairs]
    baseline = statistics.median(ratios)
    reading.baseline = baseline

    if baseline < MIN_DOCUMENT_AGREEMENT:
        reading.declined = (
            f"this document's quotations agree with their sources at a median of "
            f"{baseline:.3f}, below the {MIN_DOCUMENT_AGREEMENT} needed to tell a "
            "one-word alteration from ordinary transcription difference. No "
            "quotation was assessed on this basis"
        )
        return reading

    cutoff = baseline - OUTLIER_MARGIN
    for entry, ratio in pairs:
        if ratio >= cutoff:
            continue
        existing = entry.checks.get("quote")
        if existing is not None and existing.verdict is Verdict.FAIL:
            continue  # the quotation check already said more than this can
        entry.try_set_check("quote_agreement", CheckResult(
            Verdict.NOT_CHECKABLE,
            reason=(
                f"this quotation matches its source at {ratio:.3f}, while the "
                f"other quotations in this document match theirs at a median of "
                f"{baseline:.3f}. That gap is larger than transcription "
                "difference accounts for in this document. **Read the quoted "
                "passage against the opinion.** This is not a finding that the "
                "quotation is wrong -- it is a prompt to check the one that "
                "stands out"
            ),
            sources_consulted=[AGREEMENT_SOURCE],
            confidence="medium",
        ))
        reading.flagged += 1
    return reading
